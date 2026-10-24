// SPDX-License-Identifier: Apache-2.0
using System;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;

namespace OdsDesktop
{
    public static class ModelDownload
    {
        static readonly TimeSpan InactivityTimeout = TimeSpan.FromSeconds(30);

        // Inspect explicitly: File.Exists hides access failures as absence.
        internal static bool InspectPath(string path, bool directory)
        {
            string current = Path.GetFullPath(path);
            bool leaf = true, exists = false;
            while (current != null)
            {
                try
                {
                    var attributes = File.GetAttributes(current);
                    if ((attributes & (FileAttributes.ReparsePoint | FileAttributes.Device)) != 0 ||
                        ((attributes & FileAttributes.Directory) != 0) != (!leaf || directory))
                        throw new IOException("Local AI files must use normal folders and files without links. Existing files were kept.");
                    if (leaf) exists = true;
                }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
                leaf = false;
                current = Path.GetDirectoryName(current);
            }
            return exists;
        }

        public static async Task<bool> MatchesAsync(string path, long size, string sha, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            if (!InspectPath(path, false) || new FileInfo(path).Length != size) return false;
            using (var hash = new SHA256Cng())
            using (var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1048576, true))
            {
                var buffer = new byte[1048576];
                int read;
                while ((read = await input.ReadAsync(buffer, 0, buffer.Length, token)) > 0)
                {
                    token.ThrowIfCancellationRequested();
                    hash.TransformBlock(buffer, 0, read, buffer, 0);
                }
                token.ThrowIfCancellationRequested();
                hash.TransformFinalBlock(new byte[0], 0, 0);
                return String.Equals(BitConverter.ToString(hash.Hash).Replace("-", ""), sha, StringComparison.OrdinalIgnoreCase);
            }
        }

        // This is an UNVERIFIED launch candidate, never proof of integrity.
        // LocalRuntime.StartAsync must hash it before creating any child.
        public static async Task<string> CandidateForLaunchAsync(ModelPin pin, string directory,
            IProgress<double> progress, CancellationToken token)
        {
            Bundle.SafeName(pin.File);
            token.ThrowIfCancellationRequested();
            string destination = Path.Combine(directory, pin.File);
            if (InspectPath(destination, false) && new FileInfo(destination).Length == pin.Size) return destination;
            return await EnsureAsync(pin, directory, progress, token);
        }

        public static Task<string> EnsureAsync(ModelPin pin, string directory,
            IProgress<double> progress, CancellationToken token)
        { return EnsureAsync(pin, directory, progress, token, InactivityTimeout); }

        internal static async Task<string> EnsureAsync(ModelPin pin, string directory,
            IProgress<double> progress, CancellationToken token, TimeSpan inactivity)
        {
            Bundle.SafeName(pin.File);
            string destination = Path.Combine(directory, pin.File);
            if (await MatchesAsync(destination, pin.Size, pin.Sha256, token)) return destination;
            return await DownloadReplacementAsync(pin, directory, progress, token, inactivity);
        }

        // Force verified acquisition for one repair retry; never skip the next
        // LocalRuntime.StartAsync integrity gate.
        public static Task<string> DownloadReplacementAsync(ModelPin pin, string directory,
            IProgress<double> progress, CancellationToken token)
        { return DownloadReplacementAsync(pin, directory, progress, token, InactivityTimeout); }

        internal static async Task<string> DownloadReplacementAsync(ModelPin pin, string directory,
            IProgress<double> progress, CancellationToken token, TimeSpan inactivity)
        {
            if (inactivity <= TimeSpan.Zero || inactivity.TotalMilliseconds > Int32.MaxValue)
                throw new ArgumentOutOfRangeException("inactivity");
            Bundle.SafeName(pin.File);
            token.ThrowIfCancellationRequested();
            InspectPath(directory, true);
            Directory.CreateDirectory(directory);
            InspectPath(directory, true);
            string destination = Path.Combine(directory, pin.File);
            InspectPath(destination, false);
            string partial = destination + ".partial-" + Guid.NewGuid().ToString("N");
            var request = (HttpWebRequest)WebRequest.Create(pin.Url);
            request.Timeout = request.ReadWriteTimeout = Math.Max(1, (int)inactivity.TotalMilliseconds);
            try
            {
                // Framework async HTTP waits require Abort, including while
                // awaiting headers. No timer limits a progressing transfer.
                using (token.Register(request.Abort))
                using (var response = await OpenAsync(request, token, inactivity))
                {
                    if (response.ContentLength >= 0 && response.ContentLength != pin.Size)
                        throw new InvalidDataException("The download size does not match the pinned model. Please try again later.");
                    using (var input = response.GetResponseStream())
                    using (var output = new FileStream(partial, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1048576, true))
                    {
                        var buffer = new byte[1048576];
                        long total = 0;
                        while (true)
                        {
                            int read;
                            using (var stall = CancellationTokenSource.CreateLinkedTokenSource(token))
                            {
                                stall.CancelAfter(inactivity);
                                using (stall.Token.Register(request.Abort))
                                {
                                    try { read = await input.ReadAsync(buffer, 0, buffer.Length, stall.Token); }
                                    catch (IOException error)
                                    { if (!token.IsCancellationRequested && !stall.IsCancellationRequested) throw; throw TransferFailure(error, token, stall.Token); }
                                    catch (WebException error)
                                    { if (!token.IsCancellationRequested && !stall.IsCancellationRequested) throw; throw TransferFailure(error, token, stall.Token); }
                                    catch (OperationCanceledException error)
                                    { throw TransferFailure(error, token, stall.Token); }
                                }
                            }
                            if (read == 0) break;
                            total += read;
                            if (total > pin.Size) throw new InvalidDataException("The model download exceeded its pinned size.");
                            await output.WriteAsync(buffer, 0, read, token);
                            if (progress != null) progress.Report((double)total / pin.Size);
                        }
                    }
                }
                if (!await MatchesAsync(partial, pin.Size, pin.Sha256, token))
                    throw new InvalidDataException("The model download failed its checksum check. Please download again.");
                token.ThrowIfCancellationRequested();
                // Preserve the old cache until replacement verification, then
                // re-inspect before mutation instead of following a target link.
                bool exists = InspectPath(destination, false);
                if (exists) File.Replace(partial, destination, null);
                else File.Move(partial, destination);
                return destination;
            }
            finally { if (File.Exists(partial)) File.Delete(partial); }
        }

        static Exception TransferFailure(Exception error, CancellationToken owner, CancellationToken wait)
        {
            if (owner.IsCancellationRequested)
                return new OperationCanceledException("Model download cancelled. You can start again.", error, owner);
            if (wait.IsCancellationRequested)
                return new TimeoutException("Model download stopped because the connection stopped responding. Check your connection and start again.", error);
            return error;
        }

        static async Task<HttpWebResponse> OpenAsync(HttpWebRequest request, CancellationToken owner, TimeSpan inactivity)
        {
            using (var header = CancellationTokenSource.CreateLinkedTokenSource(owner))
            {
                header.CancelAfter(inactivity);
                using (header.Token.Register(request.Abort))
                {
                    try { return (HttpWebResponse)await request.GetResponseAsync(); }
                    catch (WebException error)
                    { if (!owner.IsCancellationRequested && !header.IsCancellationRequested) throw; throw TransferFailure(error, owner, header.Token); }
                }
            }
        }
    }
}
