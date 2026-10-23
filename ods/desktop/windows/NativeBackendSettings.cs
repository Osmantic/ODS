// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

namespace OdsDesktop
{
    public static class NativeBackendSettings
    {
        const int MaximumBytes = 1024;
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
        static string DirectoryFor(string state) { return Path.Combine(state, "preferences"); }
        static string PathFor(string state) { return Path.Combine(DirectoryFor(state), "backend.json"); }
        public static bool ReadCuda(string state)
        {
            string directory = DirectoryFor(state), path = PathFor(state);
            if (!ModelDownload.InspectPath(directory, true)) return false;
            PortalState.PrivateDirectory(directory);
            if (!ModelDownload.InspectPath(path, false)) return false;
            var handle = CreateFile(path, 0x80000000, 1, IntPtr.Zero, 3, 0x00200000 | 0x08000000, IntPtr.Zero);
            if (handle.IsInvalid) { int error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error); }
            FileStream input = null;
            try
            {
                NativeFileInfo info;
                if (!GetFileInformationByHandle(handle, out info)) throw new Win32Exception(Marshal.GetLastWin32Error());
                if ((info.Attributes & (uint)(FileAttributes.ReparsePoint | FileAttributes.Directory | FileAttributes.Device)) != 0 || info.Links != 1)
                    throw new IOException("The saved acceleration choice must be a normal private file without links. Its bytes were kept.");
                input = new FileStream(handle, FileAccess.Read, 1024, false);
                var security = input.GetAccessControl(); var sid = WindowsIdentity.GetCurrent().User;
                var rules = security.GetAccessRules(true, true, typeof(SecurityIdentifier)).Cast<FileSystemAccessRule>().ToArray();
                if (!security.AreAccessRulesProtected || !sid.Equals(security.GetOwner(typeof(SecurityIdentifier))) ||
                    rules.Any(rule => rule.AccessControlType == AccessControlType.Allow && !sid.Equals(rule.IdentityReference)) ||
                    !rules.Any(rule => rule.AccessControlType == AccessControlType.Allow && sid.Equals(rule.IdentityReference)))
                    throw new IOException("The saved acceleration choice must be private to this Windows account. Its bytes were kept.");
                if (input.Length > MaximumBytes) throw new InvalidDataException("The saved acceleration choice is too large. Its bytes were kept.");
                using (var output = new MemoryStream())
                {
                    var buffer = new byte[1024]; int read;
                    while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        if (output.Length + read > MaximumBytes) throw new InvalidDataException("The saved acceleration choice is too large. Its bytes were kept.");
                        output.Write(buffer, 0, read);
                    }
                    Dictionary<string, object> value;
                    try { value = new JavaScriptSerializer().DeserializeObject(new UTF8Encoding(false, true).GetString(output.ToArray())) as Dictionary<string, object>; }
                    catch (ArgumentException error) { throw new InvalidDataException("The saved acceleration choice is damaged. Its bytes were kept.", error); }
                    catch (InvalidOperationException error) { throw new InvalidDataException("The saved acceleration choice is damaged. Its bytes were kept.", error); }
                    if (value == null || value.Count != 2 || !value.ContainsKey("schemaVersion") || !Equals(value["schemaVersion"], 1) ||
                        !value.ContainsKey("backend") || !(value["backend"] is string) ||
                        ((string)value["backend"] != "cpu" && (string)value["backend"] != "cuda"))
                        throw new InvalidDataException("The saved acceleration choice is unsupported. Its bytes were kept.");
                    return (string)value["backend"] == "cuda";
                }
            }
            finally { if (input != null) input.Dispose(); else handle.Dispose(); }
        }
        public static void WriteCuda(string state, bool cuda)
        {
            string directory = DirectoryFor(state), path = PathFor(state);
            PortalState.PrivateDirectory(directory);
            // Refuse unsafe or damaged prior state before staging a replacement.
            ReadCuda(state);
            var sid = WindowsIdentity.GetCurrent().User;
            var security = new FileSecurity(); security.SetAccessRuleProtection(true, false); security.SetOwner(sid);
            security.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, AccessControlType.Allow));
            byte[] bytes = new UTF8Encoding(false).GetBytes(new JavaScriptSerializer().Serialize(new { schemaVersion = 1, backend = cuda ? "cuda" : "cpu" }));
            string temporary = Path.Combine(directory, Guid.NewGuid().ToString("N") + ".partial");
            try
            {
                using (var output = new FileStream(temporary, FileMode.CreateNew, FileSystemRights.Read | FileSystemRights.Write, FileShare.None, 1024, FileOptions.None, security))
                { output.Write(bytes, 0, bytes.Length); output.Flush(true); }
                bool exists = ModelDownload.InspectPath(path, false);
                if (exists) { ReadCuda(state); File.Replace(temporary, path, null); }
                else File.Move(temporary, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
    }
}
