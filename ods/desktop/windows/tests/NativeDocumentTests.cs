// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Web.Script.Serialization;
using OdsDesktop;

static class NativeDocumentTests
{
    static int checks;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CreateHardLink(string name, string existing, IntPtr security);
    [StructLayout(LayoutKind.Sequential)] struct TokenPrivilege
    { public uint Count, Low; public int High; public uint Attributes; }
    [StructLayout(LayoutKind.Sequential)] struct PrivilegeId
    { public uint Low; public int High; }
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool LookupPrivilegeValue(string system, string name, out PrivilegeId id);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool AdjustTokenPrivileges(IntPtr token, bool disableAll, ref TokenPrivilege value, uint size,
        out TokenPrivilege previous, out uint returned);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr handle);
    static void Check(bool value, string message)
    { if (!value) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static bool Refuses<T>(Action action) where T : Exception
    { try { action(); return false; } catch (T) { return true; } }
    static PortalReceipt Response(string chat, string request, NativeDocument document = null)
    {
        return new PortalReceipt { Chat = chat, Request = request, Fingerprint = PortalState.Digest(request), State = "complete", Document = document,
            Events = document == null ? "saved text answer" : Events(document) };
    }
    static string Events(NativeDocument document)
    {
        return "data: " + Json.Serialize(new { choices = new[] { new { delta = new { content = "Your document is ready." }, finish_reason = "stop" } },
            pixel_artifacts = new { schemaVersion = 1, artifacts = new[] { document.Receipt() } } }) + "\n\ndata: [DONE]\n\n";
    }
    static string PathFor(string directory, PortalReceipt receipt)
    { return Path.Combine(directory, PortalState.Digest(receipt.Chat + "\0" + receipt.Request) + ".json"); }
    static PortalReceipt Active(string chat, string request)
    { var receipt = Response(chat, request); receipt.State = "active"; receipt.Events = ""; return receipt; }
    static void RestoreAccess(string path, byte[] original)
    {
        var security = new FileSecurity(); security.SetSecurityDescriptorBinaryForm(original, AccessControlSections.Access);
        File.SetAccessControl(path, security);
    }
    public static int Run(string root)
    {
        checks = 0;
        var document = NativeDocument.Prepare("# Xin chào\n\nTài liệu UTF-8.\n");
        Check(document.ReadBytes().Length == 32 && document.FileSha256 == "2af741033ddade1c7ef84b71a039e4dfa9c90ed36b7f6ae77ab55464272bfdad" &&
            document.SnapshotSha256 == "7244448fae54fea3410b4a7d747432fb3ab9b7378fcb50ececc7f8dea328bb86" && document.SiteId == "site-7244448fae54fea3410b4a7d",
            "Vietnamese UTF-8 bytes match the canonical independent filename/length/content snapshot vector");
        byte[] changed = document.ReadBytes(); changed[0] = 0;
        Check(document.ReadBytes()[0] == (byte)'#' && document.Content.StartsWith("# Xin chào"), "document bytes are immutable through cloned read results");
        var receiptShape = (Dictionary<string, object>)document.Receipt();
        var fileShape = (Dictionary<string, object>)receiptShape["file"];
        Check(receiptShape.Count == 6 && Equals(receiptShape["kind"], "ods-pixel-workspace-artifact") &&
            Equals(receiptShape["relativePath"], "document.md") && fileShape.Count == 3 && Equals(fileShape["path"], "document.md") && Equals(fileShape["bytes"], 32),
            "native delivery uses the exact existing Portal single-file artifact receipt contract");
        Check(NativeDocument.Prepare(new string('x', NativeDocument.MaximumBytes)).ReadBytes().Length == NativeDocument.MaximumBytes &&
            NativeDocument.Prepare(new string('đ', NativeDocument.MaximumBytes / 2)).ReadBytes().Length == NativeDocument.MaximumBytes &&
            Refuses<ArgumentException>(() => NativeDocument.Prepare(new string('đ', NativeDocument.MaximumBytes / 2 + 1))),
            "document limit counts UTF-8 bytes with an inclusive 64 KiB boundary");
        foreach (string invalid in new[] { null, " \r\n\t", "x\ud800", "x\udc00", "x\0", "x\u0001", "x\u0085" })
            Check(Refuses<ArgumentException>(() => NativeDocument.Prepare(invalid)), "blank, invalid Unicode or unsupported control document input is refused");
        Check(NativeDocument.Prepare("text\r\n\tline 😀").Content == "text\r\n\tline 😀", "newlines, tabs and valid supplementary Unicode remain exact");

        string directory = Path.Combine(root, "native-document-state"); var store = new PortalState(directory);
        var legacy = Response("legacy", "plain"); store.Save(legacy);
        var raw = (Dictionary<string, object>)Json.DeserializeObject(File.ReadAllText(PathFor(directory, legacy)));
        Check(raw.Count == 6 && Equals(raw["SchemaVersion"], 1) && !raw.ContainsKey("Document") && new PortalState(directory).Find("legacy", "plain").Document == null,
            "legacy text results preserve their exact six-field v1 schema and remain readable");
        store.Save(Active("first", "turn"));
        Check(store.ReadDocument(document.SiteId) == null && Refuses<InvalidDataException>(() => store.Save(new PortalReceipt {
            Chat = "first", Request = "turn", Fingerprint = PortalState.Digest("turn"), State = "active", Events = Events(document), Document = document })),
            "prepared bytes and active response events cannot serve or persist an unpublished document");
        var completed = Response("first", "turn", document); store.Save(completed);
        string completedPath = PathFor(directory, completed), exact = File.ReadAllText(completedPath);
        raw = (Dictionary<string, object>)Json.DeserializeObject(exact);
        var restored = new PortalState(directory);
        Check(raw.Count == 7 && Equals(raw["SchemaVersion"], 2) && Equals(raw["Document"], document.Content) &&
            completed.SchemaVersion == 2 && restored.Find("first", "turn").Document.SnapshotSha256 == document.SnapshotSha256 &&
            restored.ReadDocument(document.SiteId).ReadBytes().SequenceEqual(document.ReadBytes()),
            "one atomic completed v2 receipt durably publishes exact inline bytes and reopens without model replay");
        store.Save(Response("first", "turn", document));
        Check(File.ReadAllText(completedPath) == exact && Refuses<InvalidOperationException>(() => store.Save(Response("first", "turn", NativeDocument.Prepare("changed")))) &&
            File.ReadAllText(completedPath) == exact && Directory.GetFiles(directory, "*.json").Length == 2,
            "duplicate request snapshots are idempotent and completed document bytes cannot be overwritten");
        store.Save(Response("second", "turn", document)); store.Delete("first");
        Check(new PortalState(directory).ReadDocument(document.SiteId).FileSha256 == document.FileSha256 && store.IsDeleted("first") &&
            Refuses<InvalidOperationException>(() => store.Save(Response("first", "stale", document))),
            "deleting one shared-snapshot owner preserves the other and refuses stale publication for the deleted chat");
        store.Delete("second");
        Check(new PortalState(directory).ReadDocument(document.SiteId) == null && store.Find("legacy", "plain").Events == "saved text answer",
            "deleting the final owner removes serving authority while unrelated text history survives");

        string abrupt = Path.Combine(root, "native-document-abrupt"); var crashed = new PortalState(abrupt);
        crashed.Save(Active("abrupt", "lost")); File.WriteAllText(Path.Combine(abrupt, "uncommitted.partial"), exact, new UTF8Encoding(false));
        var recovered = new PortalState(abrupt);
        Check(recovered.Find("abrupt", "lost").State == "interrupted" && recovered.ReadDocument(document.SiteId) == null &&
            File.ReadAllText(Path.Combine(abrupt, "uncommitted.partial")) == exact,
            "crash before terminal commit preserves partial bytes but never publishes them or fabricates completion");
        LegacyOwners(root, document); CorruptReceipts(root, document); PrivatePaths(root, document); IndexedOwners(root, document); Quota(root, document);
        return checks;
    }
    static bool RestorePrivilege(Action action)
    {
        IntPtr token;
        using (var process = Process.GetCurrentProcess())
            if (!OpenProcessToken(process.Handle, 0x0020 | 0x0008, out token)) throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            PrivilegeId id;
            if (!LookupPrivilegeValue(null, "SeRestorePrivilege", out id)) throw new Win32Exception(Marshal.GetLastWin32Error());
            var enabled = new TokenPrivilege { Count = 1, Low = id.Low, High = id.High, Attributes = 2 };
            TokenPrivilege previous; uint returned;
            if (!AdjustTokenPrivileges(token, false, ref enabled, (uint)Marshal.SizeOf(typeof(TokenPrivilege)), out previous, out returned))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            int error = Marshal.GetLastWin32Error();
            if (error == 1300) return false; // This token has no restore privilege; do not elevate it.
            if (error != 0) throw new Win32Exception(error);
            try { action(); }
            finally
            {
                TokenPrivilege ignored;
                if (!AdjustTokenPrivileges(token, false, ref previous, (uint)Marshal.SizeOf(typeof(TokenPrivilege)), out ignored, out returned))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return true;
        }
        finally { CloseHandle(token); }
    }
    static SecurityIdentifier Owner(string path)
    { return (SecurityIdentifier)File.GetAccessControl(path).GetOwner(typeof(SecurityIdentifier)); }
    static void SetOwner(string path, SecurityIdentifier owner)
    { var security = new FileSecurity(); security.SetOwner(owner); File.SetAccessControl(path, security); }
    static void WriteOldFile(string path, string text)
    {
        // Match 38ba2cdee Save/Delete: default token owner, inherited private DACL.
        using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        { byte[] bytes = new UTF8Encoding(false).GetBytes(text); file.Write(bytes, 0, bytes.Length); file.Flush(true); }
    }
    static void LegacyOwners(string root, NativeDocument document)
    {
        var sid = WindowsIdentity.GetCurrent().User;
        var administrators = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
        bool elevated = new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator);
        string directory = Path.Combine(root, "native-document-legacy-owner"); new PortalState(directory);
        var legacy = Response("legacy-default-owner", "plain");
        var deleted = Response("legacy-deleted", "plain");
        var active = Active("legacy-active", "pending");
        string path = PathFor(directory, legacy), marker = Path.Combine(directory, PortalState.Digest(deleted.Chat) + ".deleted");
        File.WriteAllText(path, Json.Serialize(legacy), new UTF8Encoding(false));
        WriteOldFile(PathFor(directory, deleted), Json.Serialize(deleted));
        WriteOldFile(PathFor(directory, active), Json.Serialize(active));
        WriteOldFile(marker, deleted.Chat);
        bool receiptAdministrator = Owner(path).Equals(administrators), markerAdministrator = Owner(marker).Equals(administrators);
        Func<string, bool> foreignAllow = saved => File.GetAccessControl(saved).GetAccessRules(true, true, typeof(SecurityIdentifier))
            .Cast<FileSystemAccessRule>().Any(rule => rule.AccessControlType == AccessControlType.Allow && !sid.Equals(rule.IdentityReference));
        Console.WriteLine("LEGACY OWNER elevated=" + elevated + " receiptAdministrators=" + receiptAdministrator +
            " tombstoneAdministrators=" + markerAdministrator + " receiptCurrentUser=" + Owner(path).Equals(sid) +
            " tombstoneCurrentUser=" + Owner(marker).Equals(sid) + " receiptForeignAllow=" + foreignAllow(path) +
            " tombstoneForeignAllow=" + foreignAllow(marker));
        var store = new PortalState(directory);
        Check(store.Find(legacy.Chat, legacy.Request).Events == legacy.Events && store.IsDeleted(deleted.Chat) &&
            store.Find(active.Chat, active.Request).State == "interrupted",
            "actual legacy default-owner receipts and tombstones reopen, including interrupted-request recovery");
        legacy.Events = "updated legacy answer"; store.Save(legacy);
        Check(new PortalState(directory).Find(legacy.Chat, legacy.Request).Events == legacy.Events && store.IsDeleted(deleted.Chat),
            "legacy default-owner receipt update remains durable without losing the existing deletion marker");
        byte[] before = File.ReadAllBytes(path);
        var security = File.GetAccessControl(path); byte[] original = security.GetSecurityDescriptorBinaryForm();
        security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.WorldSid, null), FileSystemRights.Read, AccessControlType.Allow));
        File.SetAccessControl(path, security);
        try
        {
            Check(Refuses<IOException>(() => store.Find(legacy.Chat, legacy.Request)) && File.ReadAllBytes(path).SequenceEqual(before),
                "legacy default-owner compatibility never accepts a foreign allow grant or rewrites refused bytes");
        }
        finally { RestoreAccess(path, original); }
        store.Delete(deleted.Chat); store.Delete(legacy.Chat);
        string newMarker = Path.Combine(directory, PortalState.Digest(legacy.Chat) + ".deleted");
        store.Save(Response("new-private-owner", "request", document));
        Check(!File.Exists(path) && !File.Exists(PathFor(directory, deleted)) && store.IsDeleted(deleted.Chat) && store.IsDeleted(legacy.Chat) &&
            Owner(newMarker).Equals(sid) && Owner(PathFor(directory, Response("new-private-owner", "request"))).Equals(sid),
            "legacy deletion preserves its tombstone while newly written receipts and markers explicitly belong to the user SID");

        if (!elevated)
        {
            Console.WriteLine("LIMIT legacy Administrators-owner and arbitrary-owner filesystem branches need an already elevated token; no elevation attempted.");
            return;
        }
        string documentPath = PathFor(directory, Response("new-private-owner", "request"));
        SetOwner(documentPath, administrators);
        try
        {
            Check(Owner(documentPath).Equals(administrators) && new PortalState(directory).ReadDocument(document.SiteId).FileSha256 == document.FileSha256,
                "an actual Administrators-owned document still requires its exact validated private receipt and parent");
            var parentSecurity = Directory.GetAccessControl(directory); byte[] parentOriginal = parentSecurity.GetSecurityDescriptorBinaryForm();
            parentSecurity.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.WorldSid, null), FileSystemRights.Read, AccessControlType.Allow));
            Directory.SetAccessControl(directory, parentSecurity);
            try { Check(Refuses<IOException>(() => store.Find("new-private-owner", "request")), "Administrators-owned file lookup freshly refuses a parent directory with foreign access"); }
            finally
            {
                parentSecurity = new DirectorySecurity(); parentSecurity.SetSecurityDescriptorBinaryForm(parentOriginal, AccessControlSections.Access);
                Directory.SetAccessControl(directory, parentSecurity);
            }
            security = File.GetAccessControl(documentPath); original = security.GetSecurityDescriptorBinaryForm();
            security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null), FileSystemRights.Read, AccessControlType.Allow));
            File.SetAccessControl(documentPath, security);
            try { Check(Refuses<IOException>(() => store.ReadDocument(document.SiteId)), "Administrators file ownership never permits a foreign group allow DACL"); }
            finally { RestoreAccess(documentPath, original); }
        }
        finally { SetOwner(documentPath, sid); }
        byte[] documentBefore = File.ReadAllBytes(documentPath);
        bool foreignOwner = RestorePrivilege(() =>
        {
            var foreign = new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null);
            SetOwner(documentPath, foreign);
            try
            {
                Check(Owner(documentPath).Equals(foreign) && !foreignAllow(documentPath) && Refuses<IOException>(() => new PortalState(directory)) &&
                    Refuses<IOException>(() => store.ReadDocument(document.SiteId)) && File.ReadAllBytes(documentPath).SequenceEqual(documentBefore),
                    "an actual arbitrary foreign file owner refuses reopening and document serving despite a current-user-only DACL");
            }
            finally { SetOwner(documentPath, sid); }
        });
        Console.WriteLine("LEGACY OWNER elevatedAdministratorsBranch=True foreignOwnerRestorePrivilege=" + foreignOwner);
        if (!foreignOwner) Console.WriteLine("LIMIT token lacks SeRestorePrivilege for the arbitrary-owner filesystem refusal regression.");
    }
    static void CorruptReceipts(string root, NativeDocument document)
    {
        string directory = Path.Combine(root, "native-document-corrupt"); var store = new PortalState(directory);
        var receipt = Response("corrupt", "request", document); store.Save(receipt);
        string path = PathFor(directory, receipt), good = File.ReadAllText(path);
        var variants = new List<string> { good.Replace(document.FileSha256, new string('0', 64)), good.Replace("[DONE]", "[INCOMPLETE]") };
        var raw = (Dictionary<string, object>)Json.DeserializeObject(good); raw["Document"] = "changed document bytes"; variants.Add(Json.Serialize(raw));
        raw = (Dictionary<string, object>)Json.DeserializeObject(good); raw["State"] = "active"; variants.Add(Json.Serialize(raw));
        raw = (Dictionary<string, object>)Json.DeserializeObject(good); raw.Remove("Document"); variants.Add(Json.Serialize(raw));
        foreach (string bad in variants)
        {
            File.WriteAllText(path, bad, new UTF8Encoding(false));
            Check(Refuses<InvalidDataException>(() => new PortalState(directory)) && Refuses<InvalidDataException>(() => store.ReadDocument(document.SiteId)) && File.ReadAllText(path) == bad,
                "corrupt or incomplete document metadata refuses publication while preserving exact recovery bytes");
        }
        File.WriteAllText(path, good, new UTF8Encoding(false));
        var unsupported = Response("unsupported", "v2-without-document");
        string unsupportedPath = PathFor(directory, unsupported);
        File.WriteAllText(unsupportedPath, Json.Serialize(unsupported).Replace("\"SchemaVersion\":1", "\"SchemaVersion\":2"));
        Check(Refuses<InvalidDataException>(() => new PortalState(directory)), "old unsupported schema2 without a document remains refused");
    }
    static void PrivatePaths(string root, NativeDocument document)
    {
        string directory = Path.Combine(root, "native-document-private"); var store = new PortalState(directory);
        var receipt = Response("private", "request", document); store.Save(receipt);
        string path = PathFor(directory, receipt), text = File.ReadAllText(path);
        var acl = File.GetAccessControl(path); byte[] original = acl.GetSecurityDescriptorBinaryForm();
        acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.WorldSid, null), FileSystemRights.Read, AccessControlType.Allow)); File.SetAccessControl(path, acl);
        try { Check(Refuses<IOException>(() => store.ReadDocument(document.SiteId)), "a document receipt with foreign read permissions refuses serving"); }
        finally { RestoreAccess(path, original); }
        acl = File.GetAccessControl(path);
        acl.AddAccessRule(new FileSystemAccessRule(WindowsIdentity.GetCurrent().User, FileSystemRights.ReadData, AccessControlType.Deny)); File.SetAccessControl(path, acl);
        bool denied;
        try { denied = Refuses<Win32Exception>(() => store.ReadDocument(document.SiteId)); }
        finally { RestoreAccess(path, original); }
        Check(denied && File.ReadAllText(path) == text, "failed private-file inspection/read refuses serving and preserves saved bytes");

        string foreign = Path.Combine(root, "native-document-hardlink-target.json"); File.Copy(path, foreign);
        File.Delete(path);
        if (!CreateHardLink(path, foreign, IntPtr.Zero)) throw new Win32Exception(Marshal.GetLastWin32Error());
        Check(Refuses<InvalidDataException>(() => store.ReadDocument(document.SiteId)) && File.ReadAllText(foreign) == text,
            "hardlinked receipt refuses before reading foreign bytes and preserves the foreign target");
        File.Delete(path);
        string link = Path.Combine(root, "native-document-junction");
        var command = new ProcessStartInfo(Environment.GetEnvironmentVariable("ComSpec"), "/d /c mklink /J \"" + link + "\" \"" + directory + "\"")
        { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        using (var process = Process.Start(command)) { process.StandardOutput.ReadToEnd(); string error = process.StandardError.ReadToEnd(); process.WaitForExit(); if (process.ExitCode != 0) throw new Exception(error); }
        try { Check(Refuses<IOException>(() => new PortalState(link)), "junction store root refuses without following or rewriting its target"); }
        finally { if ((File.GetAttributes(link) & FileAttributes.ReparsePoint) == 0) throw new Exception("Expected private test junction."); Directory.Delete(link); }
    }
    static void Quota(string root, NativeDocument document)
    {
        // Escaping makes actual persisted bytes larger than the UTF-8 payload.
        document = NativeDocument.Prepare(new string('\\', NativeDocument.MaximumBytes));
        string directory = Path.Combine(root, "native-document-quota"); var store = new PortalState(directory);
        store.Save(Active("document", "pending"));
        for (int index = 0; index < 32; index++)
        {
            var large = Response("budget", "large-" + index); large.Events = new string('x', 2 * 1024 * 1024 - 512);
            File.WriteAllText(PathFor(directory, large), Json.Serialize(large), new UTF8Encoding(false));
        }
        int before = Directory.GetFiles(directory).Length; string active = File.ReadAllText(PathFor(directory, Active("document", "pending")));
        long previousBytes = Directory.GetFiles(directory, "*.json").Sum(path => new FileInfo(path).Length);
        string message = null;
        try { store.Save(Response("document", "pending", document)); } catch (PortalCapacityException error) { message = error.Message; }
        Check(previousBytes < PortalState.MaximumRetainedBytes && message != null && message.Contains("no document was published") && !message.Contains("No new response was started") &&
            File.ReadAllText(PathFor(directory, Active("document", "pending"))) == active && store.ReadDocument(document.SiteId) == null &&
            Directory.GetFiles(directory).Length == before && !Directory.GetFiles(directory, "*.partial").Any(),
            "document replacement byte quota refuses before commit, preserving the active receipt and all prior results without publishing");
        var failed = Active("document", "pending"); failed.State = "interrupted"; failed.Events = "document capacity failure"; store.Save(failed);
        Check(store.Find("document", "pending").Events == "document capacity failure", "document quota never blocks saving an interruption result without an artifact");
        store.Delete("budget"); store.Save(Active("document", "retry")); store.Save(Response("document", "retry", document));
        Check(new PortalState(directory).ReadDocument(document.SiteId).SnapshotSha256 == document.SnapshotSha256 && store.IsDeleted("budget"),
            "explicit conversation deletion restores document capacity while retaining stale-writer guards");
    }
    static void IndexedOwners(string root, NativeDocument document)
    {
        string directory = Path.Combine(root, "native-document-owner-index"); var store = new PortalState(directory);
        var owner = Response("owner", "request", document);
        var unrelated = Response("unrelated", "text");
        store.Save(owner); store.Save(unrelated);
        store = new PortalState(directory);
        Check(store.ReadDocument(document.SiteId).FileSha256 == document.FileSha256, "startup builds the document owner index from validated completed receipts");
        string unrelatedPath = PathFor(directory, unrelated); File.WriteAllText(unrelatedPath, "unrelated corrupted text receipt", new UTF8Encoding(false));
        Check(store.ReadDocument(document.SiteId).ReadBytes().SequenceEqual(document.ReadBytes()) &&
            Refuses<InvalidDataException>(() => store.Find("unrelated", "text")) && File.ReadAllText(unrelatedPath) == "unrelated corrupted text receipt",
            "document reads reopen only indexed owners while unrelated corrupted history still refuses direct lookup and is preserved");
        File.WriteAllText(unrelatedPath, Json.Serialize(unrelated), new UTF8Encoding(false));
        string marker = Path.Combine(directory, PortalState.Digest("owner") + ".deleted");
        File.WriteAllText(marker, "invalid tombstone", new UTF8Encoding(false));
        Check(Refuses<InvalidDataException>(() => store.Delete("owner")) && Refuses<InvalidDataException>(() => store.ReadDocument(document.SiteId)),
            "invalid deletion marker refuses deletion and never grants indexed document serving");
        File.Delete(marker);
        bool deletionFailed;
        using (var held = new FileStream(PathFor(directory, owner), FileMode.Open, FileAccess.Read, FileShare.Read))
            deletionFailed = Refuses<IOException>(() => store.Delete("owner"));
        Check(deletionFailed && store.IsDeleted("owner") && store.ReadDocument(document.SiteId) == null && File.Exists(PathFor(directory, owner)) &&
            Refuses<InvalidOperationException>(() => store.Save(owner)),
            "failed receipt deletion after durable tombstone cannot use a stale owner index to serve the document");
        store.Delete("owner");
        Check(store.ReadDocument(document.SiteId) == null && !File.Exists(PathFor(directory, owner)) && store.Find("unrelated", "text") != null,
            "successful deletion retry removes only its indexed owner and preserves unrelated history");
    }
}
