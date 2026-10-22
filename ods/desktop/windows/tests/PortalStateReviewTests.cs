// SPDX-License-Identifier: Apache-2.0
using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using OdsDesktop;

static class PortalStateReviewTests
{
    sealed class CountingModel : IPortalModel
    {
        public int Calls;
        public bool IsRunning { get { return true; } }
        public Task<string> CompleteAsync(List<Dictionary<string, string>> messages, CancellationToken token)
        { Calls++; return Task.FromResult("unexpected replay"); }
        public Task VerifyIdleAsync() { return Task.FromResult(0); }
        public void Dispose() { }
    }
    static int checks;
    static void Check(bool value, string message)
    { if (!value) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static PortalReceipt Receipt(string chat, string request)
    { return new PortalReceipt { Chat = chat, Request = request, Fingerprint = PortalState.Digest(request), State = "complete", Events = "saved answer " + request }; }
    static string ReceiptPath(string directory, PortalReceipt receipt)
    { return Path.Combine(directory, PortalState.Digest(receipt.Chat + "\0" + receipt.Request) + ".json"); }
    static void Seed(string directory, PortalReceipt receipt)
    { File.WriteAllText(ReceiptPath(directory, receipt), new JavaScriptSerializer().Serialize(receipt), new UTF8Encoding(false)); }
    static bool Capacity(Action action)
    {
        try { action(); return false; }
        catch (PortalCapacityException error)
        { return error.Message.Contains("Export") && error.Message.Contains("Portal") && error.Message.Length < 300; }
    }
    public static int Run(string root)
    {
        string directory = Path.Combine(root, "portal-capacity-review");
        var store = new PortalState(directory);
        for (int i = 0; i < PortalState.MaximumReceipts - 1; i++) Seed(directory, Receipt("full-chat", "request-" + i));
        Seed(directory, Receipt("other-chat", "keep"));
        string partial = Path.Combine(directory, "uncommitted.partial");
        File.WriteAllText(partial, "preserve incomplete recovery bytes", new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(directory, PortalState.Digest("deleted-before") + ".deleted"), "deleted-before");

        store = new PortalState(directory);
        Check(store.Find("other-chat", "keep").Events == "saved answer keep" && store.IsDeleted("deleted-before"),
            "full receipt store still opens with durable tombstones and partial recovery files");
        int before = Directory.GetFiles(directory).Length;
        Check(Capacity(() => store.Save(Receipt("new-chat", "blocked"))) && Directory.GetFiles(directory).Length == before,
            "new admission at capacity gives actionable export and deletion recovery without writing a receipt");
        var retained = store.Find("other-chat", "keep"); retained.Events = "exact retained completion"; store.Save(retained);
        Check(store.Find("full-chat", "request-0").Events == "saved answer request-0" && store.Find("other-chat", "keep").Events == "exact retained completion",
            "capacity never evicts older answers or blocks an existing receipt update");
        var model = new CountingModel();
        var messages = new[] { new { role = "user", content = "retained owner request" } };
        string duplicate = new JavaScriptSerializer().Serialize(new { chat_id = "other-chat", request_id = "keep", messages,
            history_snapshot = new { schemaVersion = 1, messages } });
        retained.Fingerprint = PortalState.Digest(duplicate); store.Save(retained);
        using (var session = new NativePortalSession(model, directory, "fixture"))
        {
            var reply = session.HandleAsync(NativePortalSession.Origin, NativePortalSession.Origin, "POST", "/api/pixel/chat/stream", duplicate).GetAwaiter().GetResult();
            Check(reply.Status == 200 && reply.Content == "exact retained completion" && model.Calls == 0,
                "duplicate request at full capacity returns its exact retained result without replaying model work");
            string blocked = new JavaScriptSerializer().Serialize(new { chat_id = "new-chat", request_id = "blocked-request", messages,
                history_snapshot = new { schemaVersion = 1, messages } });
            reply = session.HandleAsync(NativePortalSession.Origin, NativePortalSession.Origin, "POST", "/api/pixel/chat/stream", blocked).GetAwaiter().GetResult();
            Check(reply.Status == 507 && reply.Content.Contains("native_storage_full") && reply.Content.Contains("Export") && model.Calls == 0,
                "full storage maps admission refusal to actionable HTTP 507 before any model call");
            string lookup = new JavaScriptSerializer().Serialize(new { chat_id = "new-chat", request_id = "blocked-request" });
            reply = session.HandleAsync(NativePortalSession.Origin, NativePortalSession.Origin, "POST", "/api/pixel/chat/result", lookup).GetAwaiter().GetResult();
            Check(reply.Status == 404, "storage rejection leaves no active or uncertain response receipt");
            lookup = new JavaScriptSerializer().Serialize(new { chat_id = "other-chat", request_id = "keep" });
            reply = session.HandleAsync(NativePortalSession.Origin, NativePortalSession.Origin, "POST", "/api/pixel/chat/result", lookup).GetAwaiter().GetResult();
            Check(reply.Status == 200 && reply.Content.Contains("exact retained completion") && model.Calls == 0,
                "saved-result recovery still works through the native bridge at full capacity");
            store.Delete("full-chat");
            reply = session.HandleAsync(NativePortalSession.Origin, NativePortalSession.Origin, "POST", "/api/pixel/chat/stream", blocked).GetAwaiter().GetResult();
            Check(reply.Status == 200 && model.Calls == 1,
                "explicit owner deletion frees storage and safely admits a previously refused request");
        }
        store.Save(Receipt("new-chat", "accepted"));
        store = new PortalState(directory);
        Check(store.Find("full-chat", "request-0") == null && store.IsDeleted("full-chat") &&
            store.Find("new-chat", "accepted") != null && store.Find("other-chat", "keep").Events == "exact retained completion" &&
            File.ReadAllText(partial) == "preserve incomplete recovery bytes",
            "explicit conversation deletion frees reply slots while preserving other history, tombstones and recovery bytes across reopen");

        string legacy = Path.Combine(root, "portal-over-capacity-review");
        var legacyStore = new PortalState(legacy);
        for (int i = 0; i < 257; i++)
        {
            var receipt = Receipt("legacy-chat", "legacy-" + i);
            if (i == 0) { receipt.State = "active"; receipt.Events = ""; }
            Seed(legacy, receipt);
            File.WriteAllText(Path.Combine(legacy, i + ".partial"), "preserve");
            string chat = "old-deleted-" + i;
            File.WriteAllText(Path.Combine(legacy, PortalState.Digest(chat) + ".deleted"), chat);
        }
        legacyStore = new PortalState(legacy);
        legacyStore.Save(Receipt("new", "accepted"));
        Check(legacyStore.Find("legacy-chat", "legacy-0").State == "interrupted" &&
            legacyStore.Find("legacy-chat", "legacy-256") != null && legacyStore.Find("new", "accepted") != null &&
            Directory.GetFiles(legacy, "*.partial").Length == 257,
            "257 replies plus over 256 tombstones and partials open, recover active work and admit another reply");
        for (int i = 257; i <= PortalState.MaximumReceipts; i++) Seed(legacy, Receipt("legacy-chat", "legacy-" + i));
        legacyStore = new PortalState(legacy);
        Check(legacyStore.Find("legacy-chat", "legacy-4096") != null && Capacity(() => legacyStore.Save(Receipt("new", "blocked"))),
            "legacy stores above the declared live result limit open and preserve answers while refusing only new admissions");
        legacyStore.Delete("legacy-chat"); legacyStore.Save(Receipt("new", "recovered"));
        Check(legacyStore.Find("new", "recovered") != null && legacyStore.IsDeleted("legacy-chat"),
            "legacy over-capacity state can recover through the ordinary conversation deletion contract");

        string deleted = Path.Combine(root, "portal-deletion-capacity-review");
        var deletionStore = new PortalState(deleted);
        for (int i = 0; i < PortalState.MaximumDeletedConversations; i++)
        {
            string chat = "deleted-" + i;
            File.WriteAllText(Path.Combine(deleted, PortalState.Digest(chat) + ".deleted"), chat, new UTF8Encoding(false));
        }
        deletionStore = new PortalState(deleted);
        deletionStore.Save(Receipt("keep", "answer"));
        before = Directory.GetFiles(deleted).Length;
        Check(Capacity(() => deletionStore.Delete("keep")) && Directory.GetFiles(deleted).Length == before &&
            deletionStore.Find("keep", "answer") != null && !deletionStore.IsDeleted("keep"),
            "bounded deletion safety refuses before mutation without consuming live receipt capacity or erasing history");
        string savedBeforeDelete = File.ReadAllText(ReceiptPath(deleted, Receipt("keep", "answer")));
        using (var session = new NativePortalSession(new CountingModel(), deleted, "fixture"))
        {
            var reply = session.HandleAsync(NativePortalSession.Origin, NativePortalSession.Origin, "DELETE", "/api/pixel/images/keep", "").GetAwaiter().GetResult();
            var body = new JavaScriptSerializer().DeserializeObject(reply.Content) as Dictionary<string, object>;
            object detail;
            Check(reply.Status == 507 && body != null && body.TryGetValue("detail", out detail) && detail is string &&
                ((string)detail).Length < 300 && ((string)detail).Contains("Export") &&
                Directory.GetFiles(deleted).Length == before && !deletionStore.IsDeleted("keep") &&
                File.ReadAllText(ReceiptPath(deleted, Receipt("keep", "answer"))) == savedBeforeDelete,
                "DELETE capacity refusal preserves actionable string detail and leaves the marker and saved receipt unchanged");
        }
        deletionStore.Delete("deleted-0");
        Check(new PortalState(deleted).IsDeleted("deleted-0") && deletionStore.Find("keep", "answer") != null,
            "full tombstone budget retains stale-writer protection and permits idempotent deletion retry after reopen");

        string byteBudget = Path.Combine(root, "portal-byte-capacity-review");
        var byteStore = new PortalState(byteBudget);
        for (int i = 0; i < 33; i++)
        {
            var receipt = Receipt("large-chat", "large-" + i);
            receipt.Events = new string('x', 2 * 1024 * 1024 - 512);
            Seed(byteBudget, receipt);
        }
        byteStore = new PortalState(byteBudget);
        before = Directory.GetFiles(byteBudget).Length;
        Check(new DirectoryInfo(byteBudget).GetFiles("*.json").Sum(file => file.Length) > PortalState.MaximumRetainedBytes &&
            Capacity(() => byteStore.Save(Receipt("new", "byte-blocked"))) && Directory.GetFiles(byteBudget).Length == before,
            "legacy byte budget overflow opens without eviction and refuses new receipt admission before writing");
        var largeUpdate = byteStore.Find("large-chat", "large-0"); largeUpdate.Events = "retained update"; byteStore.Save(largeUpdate);
        Check(byteStore.Find("large-chat", "large-0").Events == "retained update" && byteStore.Find("large-chat", "large-32").Events.Length > 0,
            "byte budget does not block existing result updates or discard unacknowledged answers");
        byteStore.Delete("large-chat"); byteStore.Save(Receipt("new", "byte-recovered"));
        Check(new PortalState(byteBudget).Find("new", "byte-recovered") != null && byteStore.IsDeleted("large-chat"),
            "explicit conversation deletion releases the byte budget and preserves its durable stale-writer marker");

        string oversized = Path.Combine(root, "portal-oversized-result-review");
        var sizeStore = new PortalState(oversized);
        var large = Receipt("large", "request"); large.Events = new string('x', 2 * 1024 * 1024);
        bool refused = false;
        try { sizeStore.Save(large); } catch (InvalidDataException) { refused = true; }
        Check(refused && !Directory.GetFiles(oversized).Any(),
            "oversized saved result is refused before it can create an unreadable result or partial file");
        return checks;
    }
}
