// SPDX-License-Identifier: Apache-2.0
using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace OdsDesktop
{
    public static class RuntimeDownload
    {
        public static async Task<string> BundledAsync(Bundle bundle, string package, CancellationToken token)
        {
            if (!bundle.RuntimeBundled) throw new InvalidOperationException("This package uses checked upstream runtime acquisition.");
            string directory = Path.Combine(package, "runtime");
            if (!Directory.Exists(directory) || (File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("The native runtime package is incomplete. Reinstall ODS Windows Preview.");
            foreach (var pin in bundle.Files)
                if ((File.GetAttributes(Path.Combine(directory, pin.Name)) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("Native runtime package files must not be links.");
            if (!await MatchesAsync(directory, bundle, token))
                throw new InvalidDataException("The packaged native runtime failed verification. Reinstall ODS Windows Preview; saved conversations are kept.");
            return directory;
        }
        public static async Task<bool> MatchesAsync(string directory, Bundle bundle, CancellationToken token)
        {
            foreach (var pin in bundle.Files)
            {
                Bundle.SafeName(pin.Name);
                string path = Path.Combine(directory, pin.Name);
                if (!File.Exists(path) || !await ModelDownload.MatchesAsync(path, new FileInfo(path).Length, pin.Sha256, token)) return false;
            }
            return Directory.Exists(directory) && Directory.GetFiles(directory).Length == bundle.Files.Length;
        }
        public static async Task<string> EnsureAsync(Bundle bundle, string state, IProgress<double> progress, CancellationToken token)
        {
            string runtimes = Path.Combine(state, "runtimes");
            string runtime = Path.Combine(runtimes, Path.GetFileNameWithoutExtension(bundle.Runtime.File));
            if (await MatchesAsync(runtime, bundle, token)) return runtime;
            string archive = await ModelDownload.EnsureAsync(bundle.Runtime, Path.Combine(state, "downloads"), progress, token);
            Directory.CreateDirectory(runtimes);
            string stage = Path.Combine(runtimes, "stage-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(stage);
            try
            {
                using (var zip = ZipFile.OpenRead(archive))
                {
                    foreach (var pin in bundle.Files)
                    {
                        token.ThrowIfCancellationRequested();
                        Bundle.SafeName(pin.Name);
                        var entry = zip.Entries.SingleOrDefault(item => item.FullName == pin.Name);
                        if (entry == null) throw new InvalidDataException("The runtime archive is incomplete. Please download it again.");
                        using (var input = entry.Open())
                        using (var output = new FileStream(Path.Combine(stage, pin.Name), FileMode.CreateNew, FileAccess.Write, FileShare.None, 65536, true))
                            await input.CopyToAsync(output, 65536, token);
                    }
                }
                if (!await MatchesAsync(stage, bundle, token)) throw new InvalidDataException("The extracted runtime failed verification.");
                token.ThrowIfCancellationRequested();
                string previous = runtime + ".previous-" + Guid.NewGuid().ToString("N");
                bool moved = false;
                if (Directory.Exists(runtime))
                {
                    if ((File.GetAttributes(runtime) & FileAttributes.ReparsePoint) != 0)
                        throw new IOException("The runtime cache is a link. Use a normal local application-data folder.");
                    Directory.Move(runtime, previous); moved = true;
                }
                try { Directory.Move(stage, runtime); }
                catch { if (moved) Directory.Move(previous, runtime); throw; }
                // Keep a damaged previous tree for owner inspection rather than
                // recursively removing an unexpected owner-supplied link/tree.
                return runtime;
            }
            finally { if (Directory.Exists(stage)) Directory.Delete(stage, true); }
        }
    }
}
