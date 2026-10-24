// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

namespace OdsDesktop
{
    public sealed class PortalCapacityException : IOException
    {
        public PortalCapacityException(string message) : base(message) { }
    }
    public sealed class PortalReceipt
    {
        public int SchemaVersion = 1;
        public string Chat, Request, Fingerprint, State, Events;
        [ScriptIgnore] public NativeDocument Document;
    }
    public sealed class PortalState
    {
        public const int MaximumReceipts = 4096;
        public const long MaximumRetainedBytes = 64L * 1024 * 1024;
        public const int MaximumDeletedConversations = 4096;
        const int MaximumReceiptBytes = 2 * 1024 * 1024;
        readonly string directory;
        readonly Dictionary<string, HashSet<string>> documents = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal);
        readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
        [StructLayout(LayoutKind.Sequential)] struct NativeFileInfo
        {
            public uint Attributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME Creation, Access, Write;
            public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetFileInformationByHandle(SafeFileHandle file, out NativeFileInfo info);
        public static string Digest(string text)
        {
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").ToLowerInvariant();
        }
        public static string PrivateDirectory(string path)
        {
            ModelDownload.InspectPath(path, true);
            var sid = WindowsIdentity.GetCurrent().User;
            var security = new DirectorySecurity();
            security.SetAccessRuleProtection(true, false); security.SetOwner(sid);
            security.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl,
                InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
            bool exists = true;
            try
            {
                if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("The Portal data directory must not be a link.");
            }
            catch (FileNotFoundException) { exists = false; }
            catch (DirectoryNotFoundException) { exists = false; }
            if (!exists) Directory.CreateDirectory(path, security);
            ModelDownload.InspectPath(path, true);
            var actual = new DirectoryInfo(path).GetAccessControl();
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0 || !actual.AreAccessRulesProtected ||
                !sid.Equals(actual.GetOwner(typeof(SecurityIdentifier))) || actual.GetAccessRules(true, true, typeof(SecurityIdentifier))
                    .Cast<FileSystemAccessRule>().Any(rule => rule.AccessControlType == AccessControlType.Allow && !sid.Equals(rule.IdentityReference)))
                throw new IOException("The Portal data directory must be private to this Windows account and must not be a link.");
            return path;
        }
        public PortalState(string directory)
        {
            this.directory = PrivateDirectory(directory);
            var files = Directory.GetFiles(directory);
            // Capacity only prevents a new admission. Existing answers and
            // deletion must remain reachable, including older oversized stores.
            foreach (string path in files)
            {
                // Unknown or partial files are retained, never normalized/deleted.
                if (!path.EndsWith(".json", StringComparison.Ordinal)) continue;
                var receipt = Read(path);
                if (receipt.State == "active")
                {
                    receipt.State = "interrupted"; receipt.Events = NativePortalSession.FailureEvents;
                    Save(receipt);
                }
                IndexDocument(receipt, path);
            }
        }
        string PathFor(string chat, string request) { return Path.Combine(directory, Digest(chat + "\0" + request) + ".json"); }
        FileStream OpenPrivate(string path)
        {
            if (!ModelDownload.InspectPath(path, false)) throw new FileNotFoundException("The saved Portal file is unavailable.");
            // OPEN_REPARSE_POINT opens a changed leaf link itself, never its
            // target. Deny concurrent writers and deletion for the whole read.
            var handle = CreateFile(path, 0x80000000, 1, IntPtr.Zero, 3, 0x00200000 | 0x08000000, IntPtr.Zero);
            if (handle.IsInvalid) { int error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error); }
            FileStream input = null;
            try
            {
                NativeFileInfo info;
                if (!GetFileInformationByHandle(handle, out info)) throw new Win32Exception(Marshal.GetLastWin32Error());
                if ((info.Attributes & (uint)(FileAttributes.ReparsePoint | FileAttributes.Directory | FileAttributes.Device)) != 0 || info.Links != 1)
                    throw new InvalidDataException("Saved Portal files must be normal private files without links. Their bytes were preserved.");
                input = new FileStream(handle, FileAccess.Read, 8192, false);
                var security = input.GetAccessControl();
                var sid = WindowsIdentity.GetCurrent().User;
                var owner = (SecurityIdentifier)security.GetOwner(typeof(SecurityIdentifier));
                // The text-only preview used the token's default file owner.
                // Elevated tokens can default to BUILTIN\Administrators even
                // inside our SID-owned, current-user-only private directory.
                // This privileged owner does not authorize any foreign DACL
                // grant; arbitrary owners remain refused for every schema.
                bool defaultAdministratorOwner = owner.IsWellKnown(WellKnownSidType.BuiltinAdministratorsSid);
                if ((!sid.Equals(owner) && !defaultAdministratorOwner) || security.GetAccessRules(true, true, typeof(SecurityIdentifier))
                    .Cast<FileSystemAccessRule>().Any(rule => rule.AccessControlType == AccessControlType.Allow && !sid.Equals(rule.IdentityReference)))
                    throw new IOException("Saved Portal files must be private to this Windows account. Their bytes were preserved.");
                if (defaultAdministratorOwner) PrivateDirectory(directory);
                return input;
            }
            catch { if (input != null) input.Dispose(); else handle.Dispose(); throw; }
        }
        string ReadPrivate(string path, int maximum)
        {
            using (var input = OpenPrivate(path))
            using (var output = new MemoryStream())
            {
                if (input.Length > maximum) throw new InvalidDataException("A saved Portal file exceeds its supported size. Its bytes were preserved.");
                var buffer = new byte[8192]; int read;
                while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
                {
                    if (output.Length + read > maximum) throw new InvalidDataException("A saved Portal file exceeds its supported size. Its bytes were preserved.");
                    output.Write(buffer, 0, read);
                }
                byte[] bytes = output.ToArray();
                int offset = bytes.Length >= 3 && bytes[0] == 239 && bytes[1] == 187 && bytes[2] == 191 ? 3 : 0;
                try { return new UTF8Encoding(false, true).GetString(bytes, offset, bytes.Length - offset); }
                catch (DecoderFallbackException error) { throw new InvalidDataException("A saved Portal file contains invalid UTF-8. Its bytes were preserved.", error); }
            }
        }
        long StoredLength(string path) { using (var input = OpenPrivate(path)) return input.Length; }
        PortalReceipt Read(string path)
        {
            string text = ReadPrivate(path, MaximumReceiptBytes);
            Dictionary<string, object> raw;
            try { raw = json.DeserializeObject(text) as Dictionary<string, object>; }
            catch (ArgumentException error) { throw new InvalidDataException("A saved Portal result contains invalid JSON. Its bytes were preserved.", error); }
            catch (InvalidOperationException error) { throw new InvalidDataException("A saved Portal result contains invalid JSON. Its bytes were preserved.", error); }
            var fields = new[] { "Chat", "Request", "Fingerprint", "State", "Events" };
            object version;
            bool document = raw != null && raw.TryGetValue("SchemaVersion", out version) && Equals(version, 2);
            if (raw == null || raw.Count != (document ? 7 : 6) || !raw.ContainsKey("SchemaVersion") ||
                !Equals(raw["SchemaVersion"], document ? 2 : 1) || fields.Any(key => !raw.ContainsKey(key) || !(raw[key] is string)) ||
                document && (!raw.ContainsKey("Document") || !(raw["Document"] is string)))
                throw new InvalidDataException("The saved Portal result schema is unsupported. Its bytes have been preserved.");
            var receipt = new PortalReceipt { SchemaVersion = document ? 2 : 1, Chat = (string)raw["Chat"], Request = (string)raw["Request"],
                Fingerprint = (string)raw["Fingerprint"], State = (string)raw["State"], Events = (string)raw["Events"] };
            if (receipt == null || !NativePortalSession.SafeId(receipt.Chat) || !NativePortalSession.SafeId(receipt.Request) ||
                path != PathFor(receipt.Chat, receipt.Request) || receipt.Fingerprint == null || !Regex.IsMatch(receipt.Fingerprint, "\\A[a-f0-9]{64}\\z") ||
                !new[] { "active", "complete", "interrupted", "cancelled" }.Contains(receipt.State) || receipt.Events == null)
                throw new InvalidDataException("A saved Portal result is invalid. It has been preserved for recovery.");
            if (document)
            {
                if (receipt.State != "complete") throw new InvalidDataException("Only a completed Portal result can publish a document.");
                try { receipt.Document = NativeDocument.Prepare((string)raw["Document"]); }
                catch (ArgumentException error) { throw new InvalidDataException("The saved document is invalid. Its bytes were preserved.", error); }
                ValidateDocumentEvents(receipt.Events, receipt.Document);
            }
            return receipt;
        }
        public PortalReceipt Find(string chat, string request)
        {
            string path = PathFor(chat, request);
            return ModelDownload.InspectPath(path, false) ? Read(path) : null;
        }
        void ValidateDocumentEvents(string events, NativeDocument document)
        {
            bool published = false, done = false;
            foreach (string line in events.Split('\n'))
            {
                if (!line.StartsWith("data:", StringComparison.Ordinal)) continue;
                if (done) throw new InvalidDataException("The saved document has events after completion.");
                string payload = line.Substring(5).Trim();
                if (payload == "[DONE]") { done = true; continue; }
                Dictionary<string, object> frame;
                try { frame = json.DeserializeObject(payload) as Dictionary<string, object>; }
                catch (ArgumentException error) { throw new InvalidDataException("The saved document has invalid response events.", error); }
                catch (InvalidOperationException error) { throw new InvalidDataException("The saved document has invalid response events.", error); }
                if (frame == null || frame.ContainsKey("error")) throw new InvalidDataException("The saved document belongs to an incomplete response.");
                object markerValue;
                if (!frame.TryGetValue("pixel_artifacts", out markerValue)) continue;
                var marker = markerValue as Dictionary<string, object>;
                object schema, artifactsValue, choicesValue;
                if (published || marker == null || marker.Count != 2 || !marker.TryGetValue("schemaVersion", out schema) || !Equals(schema, 1) ||
                    !marker.TryGetValue("artifacts", out artifactsValue) || !(artifactsValue is object[]) ||
                    !frame.TryGetValue("choices", out choicesValue) || !(choicesValue is object[]))
                    throw new InvalidDataException("The saved document receipt is invalid.");
                var artifacts = (object[])artifactsValue; var choices = (object[])choicesValue;
                var choice = choices.Length == 1 ? choices[0] as Dictionary<string, object> : null;
                object finish;
                if (artifacts.Length != 1 || !document.MatchesReceipt(artifacts[0]) || choice == null ||
                    !choice.TryGetValue("finish_reason", out finish) || !Equals(finish, "stop"))
                    throw new InvalidDataException("The saved document bytes do not match their completed delivery receipt.");
                published = true;
            }
            if (!published || !done) throw new InvalidDataException("The saved document has no completed delivery receipt.");
        }
        public NativeDocument ReadDocument(string siteId)
        {
            if (siteId == null || !Regex.IsMatch(siteId, "\\Asite-[a-f0-9]{24}\\z")) return null;
            PrivateDirectory(directory);
            HashSet<string> owners;
            if (!documents.TryGetValue(siteId, out owners)) return null;
            NativeDocument result = null;
            // This index selects candidates only. Reopen the owning receipt and
            // validate its exact private bytes and tombstone on every request.
            foreach (string path in owners)
            {
                var receipt = Read(path);
                if (receipt.State != "complete" || receipt.Document == null || receipt.Document.SiteId != siteId || IsDeleted(receipt.Chat)) continue;
                if (result != null && result.SnapshotSha256 != receipt.Document.SnapshotSha256)
                    throw new InvalidDataException("The saved snapshot identity is ambiguous. No document was served.");
                result = receipt.Document;
            }
            return result;
        }
        void IndexDocument(PortalReceipt receipt, string path)
        {
            if (receipt.State != "complete" || receipt.Document == null) return;
            HashSet<string> owners;
            if (!documents.TryGetValue(receipt.Document.SiteId, out owners))
                documents.Add(receipt.Document.SiteId, owners = new HashSet<string>(StringComparer.Ordinal));
            owners.Add(path);
        }
        void UnindexDocument(string path)
        {
            foreach (string site in documents.Keys.ToArray())
            {
                documents[site].Remove(path);
                if (documents[site].Count == 0) documents.Remove(site);
            }
        }
        byte[] Serialize(PortalReceipt receipt)
        {
            if (receipt == null || !NativePortalSession.SafeId(receipt.Chat) || !NativePortalSession.SafeId(receipt.Request) ||
                receipt.Fingerprint == null || !Regex.IsMatch(receipt.Fingerprint, "\\A[a-f0-9]{64}\\z") || receipt.Events == null ||
                !new[] { "active", "complete", "interrupted", "cancelled" }.Contains(receipt.State) ||
                receipt.SchemaVersion != 1 && receipt.SchemaVersion != 2 || receipt.SchemaVersion == 2 && receipt.Document == null)
                throw new InvalidDataException("The Portal result is invalid. Existing results were preserved.");
            var value = new Dictionary<string, object> { {"SchemaVersion", receipt.Document == null ? 1 : 2},
                {"Chat", receipt.Chat}, {"Request", receipt.Request}, {"Fingerprint", receipt.Fingerprint}, {"State", receipt.State}, {"Events", receipt.Events} };
            if (receipt.Document != null)
            {
                if (receipt.State != "complete") throw new InvalidDataException("Only a completed Portal result can publish a document.");
                ValidateDocumentEvents(receipt.Events, receipt.Document);
                value.Add("Document", receipt.Document.Content);
            }
            return new UTF8Encoding(false, true).GetBytes(json.Serialize(value));
        }
        static FileStream CreatePrivate(string path)
        {
            var sid = WindowsIdentity.GetCurrent().User;
            var security = new FileSecurity(); security.SetAccessRuleProtection(true, false); security.SetOwner(sid);
            security.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, AccessControlType.Allow));
            return new FileStream(path, FileMode.CreateNew, FileSystemRights.Read | FileSystemRights.Write,
                FileShare.None, 8192, FileOptions.None, security);
        }
        public void Save(PortalReceipt receipt)
        {
            PrivateDirectory(directory);
            byte[] bytes = Serialize(receipt);
            string path = PathFor(receipt.Chat, receipt.Request);
            if (bytes.Length > MaximumReceiptBytes)
                throw new InvalidDataException("The saved Portal result exceeds the supported size. Existing saved results have been preserved.");
            bool exists = ModelDownload.InspectPath(path, false);
            PortalReceipt previous = exists ? Read(path) : null;
            if (receipt.Document != null && IsDeleted(receipt.Chat)) throw new InvalidOperationException("This conversation was deleted. Its document cannot be published.");
            if (previous != null && previous.Document != null)
            {
                if (receipt.Document == null || receipt.Document.SnapshotSha256 != previous.Document.SnapshotSha256 ||
                    receipt.Events != previous.Events || receipt.Fingerprint != previous.Fingerprint || receipt.State != "complete")
                    throw new InvalidOperationException("A completed native document snapshot cannot be overwritten.");
                receipt.SchemaVersion = 2; return;
            }
            if (receipt.Document != null && previous != null && (previous.State != "active" || previous.Fingerprint != receipt.Fingerprint))
                throw new InvalidOperationException("Only the matching active response can commit a new document.");
            if (!exists || receipt.Document != null)
            {
                var receipts = Directory.GetFiles(directory, "*.json");
                long retained = receipts.Sum(saved => StoredLength(saved)) - (exists ? StoredLength(path) : 0) + bytes.LongLength;
                if (!exists && receipts.Length >= MaximumReceipts || retained > MaximumRetainedBytes)
                    throw new PortalCapacityException(receipt.Document == null
                        ? "Saved-result storage is full. Export conversations you want to keep, then delete a conversation in Portal before sending again. Restarting local AI does not free this space. No new response was started."
                        : "The document could not be saved because Portal storage is full. Export conversations you want to keep, then delete a conversation in Portal and try again. Existing history was kept; no document was published.");
            }
            string temporary = Path.Combine(directory, Guid.NewGuid().ToString("N") + ".partial");
            try
            {
                using (var file = CreatePrivate(temporary))
                { file.Write(bytes, 0, bytes.Length); file.Flush(true); }
                ModelDownload.InspectPath(path, false);
                if (exists) File.Replace(temporary, path, null);
                else File.Move(temporary, path);
                receipt.SchemaVersion = receipt.Document == null ? 1 : 2;
                IndexDocument(receipt, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
        public void Delete(string chat)
        {
            PrivateDirectory(directory);
            string marker = Path.Combine(directory, Digest(chat) + ".deleted");
            if (!ModelDownload.InspectPath(marker, false))
            {
                // Tombstones cannot expire: an old browser writer could then
                // resurrect a deleted chat. Bound new markers independently of
                // live results, and never evict an existing identity.
                if (Directory.GetFiles(directory, "*.deleted").Length >= MaximumDeletedConversations)
                    throw new PortalCapacityException("The saved deletion-safety limit is full. Your history is unchanged. Export your conversations, close ODS, and back up or reset its separate Portal data before deleting more conversations.");
                using (var file = CreatePrivate(marker))
                { byte[] bytes = Encoding.UTF8.GetBytes(chat); file.Write(bytes, 0, bytes.Length); file.Flush(true); }
            }
            if (!IsDeleted(chat)) throw new IOException("Conversation deletion could not be confirmed.");
            foreach (string path in Directory.GetFiles(directory, "*.json"))
                if (Read(path).Chat == chat) { File.Delete(path); UnindexDocument(path); }
        }
        public bool IsDeleted(string chat)
        {
            string marker = Path.Combine(directory, Digest(chat) + ".deleted");
            if (!ModelDownload.InspectPath(marker, false)) return false;
            if (ReadPrivate(marker, 128) != chat)
                throw new InvalidDataException("The saved conversation deletion could not be verified.");
            return true;
        }
    }
}
