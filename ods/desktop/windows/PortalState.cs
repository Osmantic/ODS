// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace OdsDesktop
{
    public sealed class PortalReceipt
    {
        public int SchemaVersion = 1;
        public string Chat, Request, Fingerprint, State, Events;
    }
    public sealed class PortalState
    {
        readonly string directory;
        readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
        public static string Digest(string text)
        {
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").ToLowerInvariant();
        }
        public static string PrivateDirectory(string path)
        {
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
            if (files.Length > 256) throw new IOException("The preview's saved-result limit was reached. Export your conversations before clearing its separate Portal data.");
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
            }
        }
        string PathFor(string chat, string request) { return Path.Combine(directory, Digest(chat + "\0" + request) + ".json"); }
        PortalReceipt Read(string path)
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0 || new FileInfo(path).Length > 2 * 1024 * 1024)
                throw new InvalidDataException("A saved Portal result is invalid. It has been preserved for recovery.");
            string text = File.ReadAllText(path);
            var raw = json.DeserializeObject(text) as Dictionary<string, object>;
            var fields = new[] { "Chat", "Request", "Fingerprint", "State", "Events" };
            if (raw == null || raw.Count != 6 || !raw.ContainsKey("SchemaVersion") || !Equals(raw["SchemaVersion"], 1) || fields.Any(key => !raw.ContainsKey(key) || !(raw[key] is string)))
                throw new InvalidDataException("The saved Portal result schema is unsupported. Its bytes have been preserved.");
            var receipt = json.Deserialize<PortalReceipt>(text);
            if (receipt == null || !NativePortalSession.SafeId(receipt.Chat) || !NativePortalSession.SafeId(receipt.Request) ||
                path != PathFor(receipt.Chat, receipt.Request) || receipt.Fingerprint == null || !Regex.IsMatch(receipt.Fingerprint, "\\A[a-f0-9]{64}\\z") ||
                !new[] { "active", "complete", "interrupted", "cancelled" }.Contains(receipt.State) || receipt.Events == null)
                throw new InvalidDataException("A saved Portal result is invalid. It has been preserved for recovery.");
            return receipt;
        }
        public PortalReceipt Find(string chat, string request)
        {
            string path = PathFor(chat, request);
            return File.Exists(path) ? Read(path) : null;
        }
        public void Save(PortalReceipt receipt)
        {
            string path = PathFor(receipt.Chat, receipt.Request);
            if (!File.Exists(path) && Directory.GetFiles(directory).Length >= 256)
                throw new IOException("The preview's saved-result limit was reached. No new response was started.");
            string temporary = Path.Combine(directory, Guid.NewGuid().ToString("N") + ".partial");
            try
            {
                byte[] bytes = Encoding.UTF8.GetBytes(json.Serialize(receipt));
                using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                { file.Write(bytes, 0, bytes.Length); file.Flush(true); }
                if (File.Exists(path)) File.Replace(temporary, path, null);
                else File.Move(temporary, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
        public void Delete(string chat)
        {
            string marker = Path.Combine(directory, Digest(chat) + ".deleted");
            if (!File.Exists(marker))
            {
                using (var file = new FileStream(marker, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                { byte[] bytes = Encoding.UTF8.GetBytes(chat); file.Write(bytes, 0, bytes.Length); file.Flush(true); }
            }
            if (!IsDeleted(chat)) throw new IOException("Conversation deletion could not be confirmed.");
            foreach (string path in Directory.GetFiles(directory, "*.json"))
                if (Read(path).Chat == chat) File.Delete(path);
        }
        public bool IsDeleted(string chat)
        {
            string marker = Path.Combine(directory, Digest(chat) + ".deleted");
            if (!File.Exists(marker)) return false;
            if ((File.GetAttributes(marker) & FileAttributes.ReparsePoint) != 0 || new FileInfo(marker).Length > 128 || File.ReadAllText(marker) != chat)
                throw new InvalidDataException("The saved conversation deletion could not be verified.");
            return true;
        }
    }
}
