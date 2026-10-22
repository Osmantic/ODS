// SPDX-License-Identifier: Apache-2.0
using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace OdsDesktop
{
    public static class PortalPackage
    {
        public static FilePin[] ReadManifest(string directory)
        { return new JavaScriptSerializer().Deserialize<FilePin[]>(File.ReadAllText(Path.Combine(directory, "portal.json"))); }
        public static bool SafePath(string name)
        {
            return !String.IsNullOrEmpty(name) && !name.Contains('\\') && name.Split('/').All(part =>
                !String.IsNullOrEmpty(part) && part != "." && part != ".." && part.IndexOfAny(Path.GetInvalidFileNameChars()) < 0);
        }
        public static async Task VerifyAsync(string directory, string sha, CancellationToken token)
        {
            string manifest = Path.Combine(directory, "portal.json");
            if (!File.Exists(manifest) || !await ModelDownload.MatchesAsync(manifest, new FileInfo(manifest).Length, sha, token))
                throw new InvalidDataException("The Portal package failed verification. Extract the complete ZIP again.");
            var files = ReadManifest(directory);
            if (files == null || files.Length < 5 || files.Length > 512 || files.Select(pin => pin.Name).Distinct(StringComparer.OrdinalIgnoreCase).Count() != files.Length ||
                !files.Any(pin => pin.Name == "OdsPortal.dll") || !files.Any(pin => pin.Name == "portal/native.html"))
                throw new InvalidDataException("The Portal package is incomplete.");
            foreach (var pin in files)
            {
                if (!SafePath(pin.Name)) throw new InvalidDataException("Invalid Portal package path.");
                string path = directory;
                foreach (string part in pin.Name.Split('/'))
                {
                    path = Path.Combine(path, part);
                    if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new IOException("Portal package paths must not be links.");
                }
                if (!await ModelDownload.MatchesAsync(path, new FileInfo(path).Length, pin.Sha256, token))
                    throw new InvalidDataException("A Portal package file failed verification. Extract the complete ZIP again.");
            }
        }
    }
}
