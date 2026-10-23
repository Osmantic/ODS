// SPDX-License-Identifier: Apache-2.0
using System;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using OdsDesktop;

static class NativeBackendSettingsTests
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CreateHardLink(string name, string existing, IntPtr security);
    static int checks;
    static void Check(bool condition, string message)
    { if (!condition) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static bool Refuses(Action action)
    {
        try { action(); return false; }
        catch (InvalidDataException) { return true; }
        catch (IOException) { return true; }
        catch (UnauthorizedAccessException) { return true; }
        catch (Win32Exception) { return true; }
    }
    static void RestoreFile(string path, byte[] descriptor)
    { var acl = new FileSecurity(); acl.SetSecurityDescriptorBinaryForm(descriptor, AccessControlSections.Access); File.SetAccessControl(path, acl); }
    public static int Run(string root)
    {
        checks = 0;
        string state = Path.Combine(root, "backend-settings"), directory = Path.Combine(state, "preferences"), path = Path.Combine(directory, "backend.json");
        Check(!NativeBackendSettings.ReadCuda(state) && !Directory.Exists(state), "missing backend settings select CPU without creating state");
        NativeBackendSettings.WriteCuda(state, true);
        var sid = WindowsIdentity.GetCurrent().User; var acl = File.GetAccessControl(path);
        Check(NativeBackendSettings.ReadCuda(state) && acl.AreAccessRulesProtected && sid.Equals(acl.GetOwner(typeof(SecurityIdentifier))) &&
            new DirectoryInfo(directory).GetAccessControl().AreAccessRulesProtected, "explicit CUDA choice reopens from an explicitly SID-owned private file and directory");
        string before = File.ReadAllText(path);
        using (var held = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            Check(Refuses(() => NativeBackendSettings.WriteCuda(state, false)), "a held settings file refuses atomic replacement");
        Check(File.ReadAllText(path) == before && NativeBackendSettings.ReadCuda(state) && Directory.GetFiles(directory).Length == 1,
            "failed replacement preserves the saved CUDA choice and removes only its own staging file");
        NativeBackendSettings.WriteCuda(state, false);
        Check(!NativeBackendSettings.ReadCuda(state) && File.ReadAllText(path).Contains("\"backend\":\"cpu\"") && Directory.GetFiles(directory).Length == 1,
            "explicit CPU choice atomically replaces CUDA without leaving partial settings");
        before = File.ReadAllText(path);
        byte[] original = acl.GetSecurityDescriptorBinaryForm(); acl = File.GetAccessControl(path);
        acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.WorldSid, null), FileSystemRights.Read, AccessControlType.Allow));
        File.SetAccessControl(path, acl);
        try
        {
            Check(Refuses(() => NativeBackendSettings.ReadCuda(state)) && Refuses(() => NativeBackendSettings.WriteCuda(state, true)) && File.ReadAllText(path) == before,
                "foreign settings grants refuse reads and writes while preserving exact prior bytes");
        }
        finally { RestoreFile(path, original); }
        string link = Path.Combine(directory, "extra-hardlink.json");
        if (!CreateHardLink(link, path, IntPtr.Zero)) throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            Check(Refuses(() => NativeBackendSettings.ReadCuda(state)) && Refuses(() => NativeBackendSettings.WriteCuda(state, true)) && File.ReadAllText(link) == before,
                "hardlinked settings refuse before following or replacing another file identity");
        }
        finally { File.Delete(link); }
        var parent = Directory.GetAccessControl(directory); byte[] parentOriginal = parent.GetSecurityDescriptorBinaryForm();
        parent.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.WorldSid, null), FileSystemRights.Read, AccessControlType.Allow));
        Directory.SetAccessControl(directory, parent);
        try { Check(Refuses(() => NativeBackendSettings.ReadCuda(state)), "a foreign-readable settings directory refuses even when the file remains private"); }
        finally
        {
            parent = new DirectorySecurity(); parent.SetSecurityDescriptorBinaryForm(parentOriginal, AccessControlSections.Access);
            Directory.SetAccessControl(directory, parent);
        }
        string broken = "{\"schemaVersion\":1,\"backend\":\"automatic\"}";
        File.WriteAllText(path, broken, new UTF8Encoding(false));
        Check(Refuses(() => NativeBackendSettings.ReadCuda(state)) && Refuses(() => NativeBackendSettings.WriteCuda(state, false)) && File.ReadAllText(path) == broken,
            "unsupported saved backend never silently resets to CPU or overwrites recovery bytes");
        return checks;
    }
}
