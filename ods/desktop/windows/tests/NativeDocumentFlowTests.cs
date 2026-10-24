// SPDX-License-Identifier: Apache-2.0
// The admitted native API, persisted result and download authority as one flow.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using OdsDesktop;

static class NativeDocumentFlowTests
{
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
    static int checks;
    static void Check(bool value, string message)
    { if (!value) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    sealed class Model : INativeDocumentModel
    {
        public bool IsRunning { get; private set; }
        public int ChatCalls, DocumentCalls, Inspections;
        public bool Hold, Fail;
        public string Text = "# Created locally\n\nA bounded native document.\n";
        public readonly TaskCompletionSource<bool> Started = new TaskCompletionSource<bool>();
        public readonly TaskCompletionSource<bool> Cancelled = new TaskCompletionSource<bool>();
        public readonly TaskCompletionSource<NativeDocumentReply> Pending = new TaskCompletionSource<NativeDocumentReply>();
        public Model() { IsRunning = true; }
        public Task<string> CompleteAsync(List<Dictionary<string, string>> messages, CancellationToken token)
        { ChatCalls++; return Task.FromResult("Ordinary chat only."); }
        public Task<NativeDocumentReply> CreateDocumentAsync(List<Dictionary<string, string>> messages, CancellationToken token)
        {
            DocumentCalls++; Started.TrySetResult(true);
            if (Fail) throw new InvalidDataException("private provider error must not be a publication receipt");
            if (!Hold) return Task.FromResult(Result());
            // Deliberately ignore cancellation until the test releases the
            // provider: even a late successful draft cannot override Stop.
            token.Register(() => Cancelled.TrySetResult(true)); return Pending.Task;
        }
        public NativeDocumentReply Result() { return new NativeDocumentReply("Your document is ready to download.", NativeDocument.Prepare(Text)); }
        public Task VerifyIdleAsync() { Inspections++; return Task.FromResult(0); }
        public void Dispose() { IsRunning = false; }
    }
    static string Turn(string chat, string request, object explicitDocument)
    {
        var messages = new[] { new { role = "user", content = "Create a document about local AI." } };
        var body = new Dictionary<string, object> { { "chat_id", chat }, { "request_id", request }, { "messages", messages },
            { "history_snapshot", new { schemaVersion = 1, messages } } };
        if (explicitDocument != null) body.Add("create_document", explicitDocument);
        return Json.Serialize(body);
    }
    static Task<PortalReply> Call(NativePortalSession session, string path, object body, string method = "POST")
    { return session.HandleAsync(NativePortalSession.Origin, NativePortalSession.Origin, method, "/api/pixel/" + path, body as string ?? Json.Serialize(body)); }
    static NativeDocument Download(NativePortalSession session, NativeDocument document)
    { return session.ReadDocument(NativePortalSession.Origin, NativePortalSession.Origin, "GET", "/pixel-preview/" + document.SiteId + "/document.md"); }
    public static async Task<int> Run(string root)
    {
        checks = 0;
        string directory = Path.Combine(root, "document-flow");
        var model = new Model(); NativeDocument expected = NativeDocument.Prepare(model.Text);
        string request = Turn("owner-a", "request-a", true), events;
        using (var session = new NativePortalSession(model, directory, "fixture"))
        {
            Check(Download(session, expected) == null, "preparing document bytes alone grants no native download authority");
            await Call(session, "chat/stream", Turn("chat-only", "plain", null));
            Check(model.ChatCalls == 1 && model.DocumentCalls == 0, "a natural-language document request cannot admit a tool without explicit owner mode");
            foreach (object invalid in new object[] { false, "true", 1 })
            {
                var refused = await Call(session, "chat/stream", Turn("owner-a", "invalid", invalid));
                Check(refused.Status == 422 && model.DocumentCalls == 0, "document admission accepts only the exact native boolean true contract");
            }
            var reply = await Call(session, "chat/stream", request); events = reply.Content;
            Check(reply.Status == 200 && events.Contains("pixel_artifacts") && events.Contains(expected.SiteId) && model.DocumentCalls == 1,
                "admitted document turn publishes a host-authored verified artifact only with its terminal reply");
            Check(Download(session, expected).ReadBytes().SequenceEqual(expected.ReadBytes()), "native download route returns the exact committed document bytes");
            reply = await Call(session, "chat/cancel", new { chat_id = "owner-a", request_id = "request-a" });
            Check(reply.Content == "{\"aborted\":false}" && Download(session, expected) != null, "late Stop preserves the already committed answer and document");
            reply = await Call(session, "chat/stream", request);
            Check(reply.Content == events && model.DocumentCalls == 1, "duplicate document identity recovers exact events without rerunning the model or tool");
            reply = await Call(session, "chat/stream", Turn("owner-a", "request-a", null));
            Check(reply.Status == 409 && model.ChatCalls == 1 && model.DocumentCalls == 1, "a retained document identity cannot be rebound to ordinary chat");
            Check(session.ReadDocument("https://foreign.invalid", NativePortalSession.Origin, "GET", "/pixel-preview/" + expected.SiteId + "/document.md") == null &&
                session.ReadDocument(NativePortalSession.Origin, "https://foreign.invalid", "GET", "/pixel-preview/" + expected.SiteId + "/document.md") == null,
                "document serving requires both private document and request origins");
            foreach (string path in new[] { "/pixel-preview/" + expected.SiteId + "/../document.md", "/pixel-preview/" + expected.SiteId + "/other.md", "/pixel-preview/" + expected.SiteId + "/%64ocument.md" })
                Check(session.ReadDocument(NativePortalSession.Origin, NativePortalSession.Origin, "GET", path) == null, "native document route refuses traversal, other names and encoded aliases");
            Check(session.ReadDocument(NativePortalSession.Origin, NativePortalSession.Origin, "POST", "/pixel-preview/" + expected.SiteId + "/document.md") == null,
                "document resource route never accepts a mutation method");
            await Call(session, "chat/stream", Turn("owner-b", "request-b", true));
            await Call(session, "images/owner-a", new { }, "DELETE");
            Check(Download(session, expected) != null, "deleting one chat retains an identical document still owned by another completed chat");
            reply = await Call(session, "chat/stream", request);
            Check(reply.Status == 410 && model.DocumentCalls == 2, "deleted document chat cannot be resurrected by a stale request writer");
        }
        var reopened = new Model();
        using (var session = new NativePortalSession(reopened, directory, "fixture"))
        {
            Check(Download(session, expected).FileSha256 == expected.FileSha256 && reopened.DocumentCalls == 0,
                "native document survives bridge restart with verified bytes and no inference replay");
            await Call(session, "images/owner-b", new { }, "DELETE");
            Check(Download(session, expected) == null, "deleting the last committed owner removes serving authority without a shared-file deletion race");
        }
        string cancelledDirectory = Path.Combine(root, "document-cancel");
        var held = new Model { Hold = true };
        using (var session = new NativePortalSession(held, cancelledDirectory, "fixture"))
        {
            var pending = Call(session, "chat/stream", Turn("cancel", "request", true)); await held.Started.Task;
            var stop = Call(session, "chat/cancel", new { chat_id = "cancel", request_id = "request" }); await held.Cancelled.Task;
            Check(Download(session, expected) == null, "in-flight document has no download authority before Stop settles");
            held.Pending.SetResult(held.Result()); await pending; var reply = await stop;
            Check(reply.Content == "{\"aborted\":true}" && held.Inspections == 1 && Download(session, expected) == null,
                "provider success after owner Stop cannot publish a staged document and requires idle inspection");
        }
        using (var session = new NativePortalSession(new Model(), cancelledDirectory, "fixture"))
            Check(Download(session, expected) == null, "cancelled document stays unpublished across restart");
        var failed = new Model { Fail = true };
        using (var session = new NativePortalSession(failed, Path.Combine(root, "document-provider-failure"), "fixture"))
        {
            var reply = await Call(session, "chat/stream", Turn("failure", "request", true));
            Check(reply.Content == NativePortalSession.DocumentFailureEvents && !reply.Content.Contains("private provider") && Download(session, expected) == null,
                "document provider failure emits only the trusted incomplete code and publishes no file");
        }
        string interruptedDirectory = Path.Combine(root, "document-dispose");
        held = new Model { Hold = true };
        using (var session = new NativePortalSession(held, interruptedDirectory, "fixture"))
        {
            var pending = Call(session, "chat/stream", Turn("closing", "request", true)); await held.Started.Task;
            session.Dispose(); held.Pending.SetResult(held.Result()); await pending;
        }
        using (var session = new NativePortalSession(new Model(), interruptedDirectory, "fixture"))
            Check(Download(session, expected) == null, "closing before durable completion preserves an interrupted receipt without publishing the late draft");

        await Quota(root);
        return checks;
    }
    static async Task Quota(string root)
    {
        string directory = Path.Combine(root, "document-flow-quota");
        var held = new Model { Hold = true };
        NativeDocument expected = NativeDocument.Prepare(held.Text);
        using (var session = new NativePortalSession(held, directory, "fixture"))
        {
            var pending = Call(session, "chat/stream", Turn("quota", "request", true)); await held.Started.Task;
            long stored = Directory.GetFiles(directory, "*.json").Sum(path => new FileInfo(path).Length);
            for (int index = 0; PortalState.MaximumRetainedBytes - stored > 256; index++)
            {
                var receipt = new PortalReceipt { Chat = "bulk", Request = "retained-" + index, Fingerprint = PortalState.Digest("retained-" + index), State = "complete", Events = "" };
                int baseline = Encoding.UTF8.GetByteCount(Json.Serialize(receipt));
                int target = (int)Math.Min(2 * 1024 * 1024 - 1, PortalState.MaximumRetainedBytes - stored - 128);
                receipt.Events = new string('x', target - baseline);
                string text = Json.Serialize(receipt);
                File.WriteAllText(Path.Combine(directory, PortalState.Digest(receipt.Chat + "\0" + receipt.Request) + ".json"), text, new UTF8Encoding(false));
                stored += Encoding.UTF8.GetByteCount(text);
            }
            held.Pending.SetResult(held.Result()); var reply = await pending;
            Check(reply.Content == NativePortalSession.DocumentStorageEvents && Download(session, expected) == null,
                "quota exhausted after admission saves a terminal failure instead of a successful document card");
            reply = await Call(session, "chat/result", new { chat_id = "quota", request_id = "request" });
            Check(reply.Content.Contains("interrupted") && reply.Content.Contains("document_storage_full"), "document quota failure is recoverable by exact result identity without replay");
            await Call(session, "images/bulk", new { }, "DELETE");
            held.Hold = false;
            reply = await Call(session, "chat/stream", Turn("quota", "retry", true));
            Check(reply.Content.Contains("pixel_artifacts") && Download(session, expected) != null && held.DocumentCalls == 2,
                "ordinary conversation deletion frees document capacity and a new explicit owner request succeeds");
        }
    }
}
