// SPDX-License-Identifier: Apache-2.0
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using OdsDesktop;

static class ModelDownloadReviewTests
{
    static int checks;
    static readonly TimeSpan Inactivity = TimeSpan.FromMilliseconds(400);
    static void Check(bool value, string message)
    { if (!value) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static string Hash(byte[] bytes)
    { using (var hash = SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    static ModelPin Pin(byte[] bytes, string url)
    { return new ModelPin { File = "fixture.gguf", Name = "fixture", Size = bytes.Length, Sha256 = Hash(bytes), Url = url }; }
    sealed class Server : IDisposable
    {
        readonly TcpListener listener = new TcpListener(IPAddress.Loopback, 0);
        readonly CancellationTokenSource stopped = new CancellationTokenSource();
        readonly Thread worker;
        volatile TcpClient client;
        Exception workerFailure;
        volatile bool disposed;
        public readonly string Url;
        public readonly TaskCompletionSource<bool> Requested = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        public Server(byte[] body, string mode)
        {
            listener.Start(); Url = "http://127.0.0.1:" + ((IPEndPoint)listener.LocalEndpoint).Port + "/model";
            // Teardown joins only this dedicated server thread. It must not
            // depend on pool continuations while the test runner waits for it.
            worker = new Thread(() =>
            {
                try
                {
                    client = listener.AcceptTcpClient();
                    using (client)
                    using (var stream = client.GetStream())
                    {
                        var reader = new StreamReader(stream, Encoding.ASCII, false, 1024, true);
                        while (!String.IsNullOrEmpty(reader.ReadLine())) { }
                        Requested.TrySetResult(true);
                        if (mode != "headers")
                        {
                            byte[] headers = Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: " + body.Length + "\r\n\r\n");
                            stream.Write(headers, 0, headers.Length);
                        }
                        if (mode == "headers" || mode == "body")
                        { stopped.Token.WaitHandle.WaitOne(); }
                        else
                        {
                            for (int offset = 0; offset < body.Length; offset += 1024)
                            {
                                stream.Write(body, offset, Math.Min(1024, body.Length - offset));
                                if (mode == "progress") Thread.Sleep(100);
                            }
                        }
                    }
                }
                catch (IOException) { /* Test clients deliberately abort failed/cancelled responses. */ }
                catch (SocketException error) { if (!disposed) workerFailure = error; }
                catch (ObjectDisposedException error) { if (!disposed) workerFailure = error; }
                catch (Exception error) { workerFailure = error; }
            }) { IsBackground = true, Name = "ODS model download loopback fixture" };
            worker.Start();
        }
        public void Dispose()
        {
            disposed = true; stopped.Cancel(); listener.Stop(); if (client != null) client.Close();
            if (!worker.Join(3000)) throw new Exception("Loopback model server did not stop.");
            stopped.Dispose();
            if (workerFailure != null) throw new Exception("Loopback model server failed.", workerFailure);
        }
    }
    static async Task<bool> Timeout(Func<Task> action)
    { try { await action(); return false; } catch (TimeoutException) { return true; } }
    static bool NoPartial(string directory)
    { return !Directory.GetFiles(directory, "*.partial-*").Any(); }
    public static async Task<int> Run(string root)
    {
        string directory = Path.Combine(root, "model-download-review"); Directory.CreateDirectory(directory);
        foreach (byte[] bytes in new[] { new byte[0], Enumerable.Range(0, 2 * 1024 * 1024 + 137).Select(i => (byte)(i * 17)).ToArray() })
        {
            string path = Path.Combine(directory, "hash-" + bytes.Length); File.WriteAllBytes(path, bytes);
            Check(await ModelDownload.MatchesAsync(path, bytes.Length, Hash(bytes), CancellationToken.None),
                "Windows CNG hash equals standard SHA256 for " + bytes.Length + " bytes");
        }
        byte[] expected = Encoding.UTF8.GetBytes("verified model fixture");
        var candidatePin = Pin(expected, "http://127.0.0.1:1/must-not-connect");
        string candidate = Path.Combine(directory, candidatePin.File);
        File.WriteAllBytes(candidate, Enumerable.Repeat((byte)'x', expected.Length).ToArray());
        using (var exclusive = new FileStream(candidate, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
        {
            Check(await ModelDownload.CandidateForLaunchAsync(candidatePin, directory, null, CancellationToken.None) == candidate,
                "unverified size-matching launch candidate is selected without reading bytes or contacting the network");
        }
        Check(!await ModelDownload.MatchesAsync(candidate, expected.Length, candidatePin.Sha256, CancellationToken.None),
            "candidate selection does not turn corrupt same-size bytes into verified model trust");
        File.WriteAllBytes(candidate, expected);
        using (var server = new Server(expected, "normal"))
        {
            candidatePin.Url = server.Url;
            await ModelDownload.DownloadReplacementAsync(candidatePin, directory, null, CancellationToken.None, Inactivity);
            Check(server.Requested.Task.IsCompleted && File.ReadAllBytes(candidate).SequenceEqual(expected) && NoPartial(directory),
                "forced replacement downloads verified bytes atomically even when the existing cache already matches its checksum");
        }
        byte[] progressive = Enumerable.Range(0, 12 * 1024).Select(i => (byte)(i * 31)).ToArray();
        using (var server = new Server(progressive, "progress"))
        {
            var watch = Stopwatch.StartNew();
            string path = await ModelDownload.EnsureAsync(Pin(progressive, server.Url), Path.Combine(directory, "progress"), null, CancellationToken.None, Inactivity);
            Check(watch.Elapsed.TotalMilliseconds > Inactivity.TotalMilliseconds * 2 && File.ReadAllBytes(path).SequenceEqual(progressive),
                "progressing HTTP download outlives repeated inactivity windows and publishes verified bytes");
        }
        foreach (string mode in new[] { "headers", "body" })
        {
            using (var server = new Server(expected, mode))
            {
                string cache = Path.Combine(directory, "stall-" + mode); Directory.CreateDirectory(cache);
                string old = Path.Combine(cache, candidatePin.File); File.WriteAllText(old, "old cache to preserve");
                var watch = Stopwatch.StartNew();
                bool timedOut = await Timeout(() => ModelDownload.DownloadReplacementAsync(Pin(expected, server.Url), cache, null, CancellationToken.None, Inactivity));
                Check(timedOut && watch.ElapsedMilliseconds < 3000 && File.ReadAllText(old) == "old cache to preserve" && NoPartial(cache),
                    "stalled HTTP " + mode + " is bounded by Abort and preserves the old cache without partial leftovers");
            }
            using (var server = new Server(expected, mode))
            using (var cancel = new CancellationTokenSource())
            {
                string cache = Path.Combine(directory, "cancel-" + mode); Directory.CreateDirectory(cache);
                string old = Path.Combine(cache, candidatePin.File); File.WriteAllText(old, "old cache to preserve");
                var pending = ModelDownload.DownloadReplacementAsync(Pin(expected, server.Url), cache, null, cancel.Token, TimeSpan.FromSeconds(5));
                await server.Requested.Task; var watch = Stopwatch.StartNew(); cancel.Cancel();
                bool cancelled = false; try { await pending; } catch (OperationCanceledException) { cancelled = true; }
                Check(cancelled && watch.ElapsedMilliseconds < 3000 && File.ReadAllText(old) == "old cache to preserve" && NoPartial(cache),
                    "owner cancellation aborts HTTP " + mode + " promptly while preserving the old cache and removing partials");
            }
        }
        using (var server = new Server(Enumerable.Repeat((byte)'x', expected.Length).ToArray(), "normal"))
        {
            string cache = Path.Combine(directory, "bad-hash"); Directory.CreateDirectory(cache);
            string old = Path.Combine(cache, candidatePin.File); File.WriteAllText(old, "old cache to preserve");
            bool refused = false;
            try { await ModelDownload.DownloadReplacementAsync(Pin(expected, server.Url), cache, null, CancellationToken.None, Inactivity); }
            catch (InvalidDataException) { refused = true; }
            Check(refused && File.ReadAllText(old) == "old cache to preserve" && NoPartial(cache),
                "a complete same-size response with a wrong checksum never replaces the old cache");
        }
        string blocked = Path.Combine(directory, "not-a-directory"); File.WriteAllText(blocked, "preserve");
        bool invalidAncestor = false;
        try { await ModelDownload.CandidateForLaunchAsync(candidatePin, Path.Combine(blocked, "child"), null, CancellationToken.None); }
        catch (IOException) { invalidAncestor = true; }
        Check(invalidAncestor && File.ReadAllText(blocked) == "preserve", "invalid cache ancestor propagates without acquisition or mutation");
        string link = Path.Combine(directory, "linked-cache"), target = Path.Combine(directory, "link-target");
        Directory.CreateDirectory(target); File.WriteAllBytes(Path.Combine(target, candidatePin.File), expected);
        var command = new ProcessStartInfo(Environment.GetEnvironmentVariable("ComSpec"), "/d /c mklink /J \"" + link + "\" \"" + target + "\"")
        { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        using (var process = Process.Start(command)) { process.StandardOutput.ReadToEnd(); string error = process.StandardError.ReadToEnd(); process.WaitForExit(); if (process.ExitCode != 0) throw new Exception(error); }
        try
        {
            bool refused = false, replacementRefused = false;
            try { await ModelDownload.CandidateForLaunchAsync(candidatePin, link, null, CancellationToken.None); }
            catch (IOException) { refused = true; }
            try { await ModelDownload.DownloadReplacementAsync(candidatePin, link, null, CancellationToken.None, Inactivity); }
            catch (IOException) { replacementRefused = true; }
            Check(refused && replacementRefused && File.ReadAllBytes(Path.Combine(target, candidatePin.File)).SequenceEqual(expected),
                "linked cache ancestors refuse candidate selection and replacement while preserving target bytes");
        }
        finally
        {
            if ((File.GetAttributes(link) & FileAttributes.ReparsePoint) == 0) throw new Exception("Expected test-owned junction before unlinking.");
            Directory.Delete(link);
        }
        return checks;
    }
}
