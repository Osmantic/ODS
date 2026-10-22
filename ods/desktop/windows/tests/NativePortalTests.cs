// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using OdsDesktop;

static class NativePortalTests
{
    public sealed class Model : IPortalModel
    {
        public bool IsRunning { get; private set; }
        public bool Hold, FailIdle;
        public int Calls, Inspections, Stops;
        public List<Dictionary<string, string>> Last;
        public TaskCompletionSource<string> Pending = new TaskCompletionSource<string>();
        public TaskCompletionSource<bool> Started = new TaskCompletionSource<bool>();
        public Model() { IsRunning = true; }
        public Task<string> CompleteAsync(List<Dictionary<string, string>> messages, CancellationToken token)
        {
            Calls++; Last = messages; Started.TrySetResult(true);
            if (!Hold) return Task.FromResult("Answer: " + messages.Last()["content"]);
            token.Register(() => Pending.TrySetCanceled()); return Pending.Task;
        }
        public Task VerifyIdleAsync() { Inspections++; if (FailIdle) throw new IOException("uninspectable slot"); return Task.FromResult(0); }
        public void Dispose() { Stops++; IsRunning = false; }
    }
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static int checks;
    static void Check(bool value, string message)
    { if (!value) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static Task<PortalReply> Call(NativePortalSession session, string path, object body, string method = "POST")
    { return session.HandleAsync(NativePortalSession.Origin, NativePortalSession.Origin, method, "/api/pixel/" + path, Json.Serialize(body)); }
    static object Turn(string chat, string request, string text)
    {
        var messages = new[] { new { role = "user", content = text } };
        return new { chat_id = chat, request_id = request, messages, history_snapshot = new { schemaVersion = 1, messages } };
    }
    public static async Task<int> Run(string root)
    {
        await Packages(root);
        string directory = Path.Combine(root, "native-portal-state");
        var model = new Model();
        using (var session = new NativePortalSession(model, directory, "fixture"))
        {
            var reply = await session.HandleAsync("https://foreign.invalid", NativePortalSession.Origin, "GET", "/api/pixel/status", "");
            Check(reply.Status == 403, "native Portal refuses an untrusted document origin");
            reply = await session.HandleAsync(NativePortalSession.Origin, "https://foreign.invalid", "POST", "/api/pixel/chat/stream", Json.Serialize(Turn("one", "request", "private")));
            Check(reply.Status == 403 && model.Calls == 0, "cross-origin native requests cannot submit model work");
            reply = await Call(session, "chat/stream", Turn("one", "request", "Xin chào"));
            Check(reply.Status == 200 && reply.Content.Contains("Answer: Xin chào") && reply.Content.Contains("[DONE]"), "real Portal request shape receives its bounded SSE completion");
            reply = await Call(session, "chat/cancel", new { chat_id = "one", request_id = "request" });
            Check(reply.Content == "{\"aborted\":false}", "late Stop preserves a completed answer before its reader consumes it");
            reply = await Call(session, "chat/result", new { chat_id = "one", request_id = "request" });
            Check(reply.Content.Contains("complete") && reply.Content.Contains("Answer: Xin chào"), "late Stop leaves the exact completed result recoverable without replay");
            reply = await Call(session, "chat/stream", Turn("one", "request", "Xin chào"));
            Check(reply.Status == 200 && model.Calls == 1, "duplicate request identity recovers the saved answer without rerunning inference");
            reply = await Call(session, "chat/stream", Turn("one", "request", "different"));
            Check(reply.Status == 409 && model.Calls == 1, "request identity cannot be rebound to a different payload");
            reply = await Call(session, "chat/stream", new { chat_id = "../escape", request_id = "other" });
            Check(reply.Status == 422 && model.Calls == 1, "invalid conversation identity is refused before model work");
            reply = await Call(session, "chat/stream", new { chat_id = "one", request_id = "bad", tools = new object[0] });
            Check(reply.Status == 422 && model.Calls == 1, "native text profile refuses unknown tool-bearing request fields");
            reply = await Call(session, "agents/start", new { chat_id = "one" });
            Check(reply.Status == 404 && model.Calls == 1, "agent and host operations are not exposed by the native bridge");
            await Call(session, "chat/stream", Turn("foreign-chat", "foreign-request", "keep"));
            reply = await Call(session, "images/one", new { }, "DELETE");
            Check(reply.Status == 200 && reply.Content.Contains("\"deleted\":true"), "existing Portal deletion contract clears only that conversation's retained results");
            reply = await Call(session, "chat/stream", Turn("one", "new-request", "stale writer"));
            Check(reply.Status == 410 && model.Calls == 2, "native deletion tombstone refuses a stale writer without inference");
        }
        using (var recovered = new NativePortalSession(model, directory, "fixture"))
        {
            var reply = await Call(recovered, "chat/result", new { chat_id = "foreign-chat", request_id = "foreign-request" });
            Check(reply.Status == 200 && reply.Content.Contains("complete") && reply.Content.Contains("Answer: keep"), "native terminal result survives a bridge restart and another chat's deletion");
        }
        var held = new Model { Hold = true };
        using (var session = new NativePortalSession(held, Path.Combine(root, "native-cancel"), "fixture"))
        {
            var pending = Call(session, "chat/stream", Turn("mine", "mine-request", "wait"));
            await held.Started.Task;
            var reply = await Call(session, "chat/stream", Turn("other", "other-request", "parallel"));
            Check(reply.Status == 409 && held.Calls == 1, "native bridge admits only one owned response at a time");
            reply = await Call(session, "chat/cancel", new { chat_id = "foreign", request_id = "mine-request" });
            Check(reply.Status == 409 && !pending.IsCompleted, "a foreign conversation cannot cancel the active response");
            reply = await Call(session, "chat/cancel", new { chat_id = "mine", request_id = "wrong" });
            Check(reply.Status == 409 && !pending.IsCompleted, "a stale request cannot cancel another turn in the same conversation");
            reply = await Call(session, "chat/cancel", new { chat_id = "mine", request_id = "mine-request" });
            await pending;
            Check(reply.Status == 200 && reply.Content == "{\"aborted\":true}" && held.Inspections == 1, "Stop is acknowledged only after the exact model request settles and its slot is inspected");
            reply = await Call(session, "chat/result", new { chat_id = "mine", request_id = "mine-request" });
            Check(reply.Content.Contains("cancelled"), "cancelled result remains recoverable after the stream reader disconnects");
        }
        var uninspectable = new Model { Hold = true, FailIdle = true };
        using (var session = new NativePortalSession(uninspectable, Path.Combine(root, "native-stop-failure"), "fixture"))
        {
            var pending = Call(session, "chat/stream", Turn("mine", "stop-request", "wait"));
            await uninspectable.Started.Task;
            var reply = await Call(session, "chat/cancel", new { chat_id = "mine", request_id = "stop-request" });
            await pending;
            Check(reply.Status == 200 && uninspectable.Stops == 1 && !uninspectable.IsRunning, "failed slot inspection stops the owned model before acknowledging Stop");
        }
        var interrupted = new Model { Hold = true };
        var closing = new NativePortalSession(interrupted, Path.Combine(root, "native-interruption"), "fixture");
        var unfinished = Call(closing, "chat/stream", Turn("mine", "interrupted-request", "wait"));
        await interrupted.Started.Task;
        closing.Dispose(); await unfinished;
        using (var recovered = new NativePortalSession(new Model(), Path.Combine(root, "native-interruption"), "fixture"))
        {
            var reply = await Call(recovered, "chat/result", new { chat_id = "mine", request_id = "interrupted-request" });
            Check(reply.Content.Contains("interrupted") && !reply.Content.Contains("Answer:"), "closing Portal preserves an interrupted receipt without replaying or inventing a completed answer");
        }
        string abrupt = Path.Combine(root, "native-abrupt-recovery");
        var store = new PortalState(abrupt);
        store.Save(new PortalReceipt { Chat = "mine", Request = "lost", Fingerprint = PortalState.Digest("request"), State = "active", Events = "" });
        var recoveryModel = new Model();
        using (var recovered = new NativePortalSession(recoveryModel, abrupt, "fixture"))
        {
            var reply = await Call(recovered, "chat/result", new { chat_id = "mine", request_id = "lost" });
            Check(reply.Content.Contains("interrupted") && recoveryModel.Calls == 0, "startup recovers a persisted active receipt as interrupted without replaying model work");
        }
        string saved = Directory.GetFiles(abrupt, "*.json").Single();
        string unsupported = File.ReadAllText(saved).Replace("\"SchemaVersion\":1", "\"SchemaVersion\":2");
        File.WriteAllText(saved, unsupported);
        bool refused = false;
        try { new NativePortalSession(recoveryModel, abrupt, "fixture"); } catch (InvalidDataException) { refused = true; }
        Check(refused && File.ReadAllText(saved) == unsupported, "unsupported retained-result schema refuses startup while preserving exact recovery bytes");
        return checks;
    }
    static async Task Packages(string root)
    {
        string package = Path.Combine(root, "portal-integrity");
        Directory.CreateDirectory(Path.Combine(package, "portal"));
        var names = new[] { "OdsPortal.dll", "portal/native.html", "portal/app.js", "portal/app.css", "portal/notice.txt" };
        foreach (string name in names) File.WriteAllText(Path.Combine(package, name), "fixture", new System.Text.UTF8Encoding(false));
        var pins = names.Select(name => new FilePin { Name = name, Sha256 = PortalState.Digest("fixture") }).ToArray();
        string manifest = Path.Combine(package, "portal.json");
        string text = Json.Serialize(pins); File.WriteAllText(manifest, text, new System.Text.UTF8Encoding(false));
        await PortalPackage.VerifyAsync(package, PortalState.Digest(text), CancellationToken.None);
        Check(true, "native Portal verifies the complete pinned host and frontend package before loading it");
        File.AppendAllText(Path.Combine(package, "portal/app.js"), "tampered");
        bool refused = false;
        try { await PortalPackage.VerifyAsync(package, PortalState.Digest(text), CancellationToken.None); } catch (InvalidDataException) { refused = true; }
        Check(refused, "modified Portal JavaScript is refused before native host loading");
        File.WriteAllText(Path.Combine(package, "portal/app.js"), "fixture", new System.Text.UTF8Encoding(false));
        File.AppendAllText(manifest, " "); refused = false;
        try { await PortalPackage.VerifyAsync(package, PortalState.Digest(text), CancellationToken.None); } catch (InvalidDataException) { refused = true; }
        Check(refused, "a modified Portal manifest cannot substitute a different trusted package");
    }
}
