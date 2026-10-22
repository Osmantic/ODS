// SPDX-License-Identifier: Apache-2.0
// The Portal conversation contract over a private, in-process WebView channel.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Runtime.ExceptionServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace OdsDesktop
{
    public sealed class PortalReply
    {
        public int Status;
        public string Content, Type;
    }
    public sealed class NativePortalSession : IDisposable
    {
        public const string Origin = "https://ods-desktop.local";
        public const string FailureEvents = "data: {\"error\":{\"type\":\"native_model_error\",\"code\":\"response_incomplete\"}}\n\ndata: [DONE]\n\n";
        readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
        readonly IPortalModel model;
        readonly string modelName;
        readonly PortalState state;
        readonly object gate = new object();
        PortalReceipt active;
        CancellationTokenSource cancellation;
        Task<PortalReply> running;
        bool disposed;
        public NativePortalSession(IPortalModel model, string directory, string modelName)
        { this.model = model; this.modelName = modelName; state = new PortalState(directory); }
        public static bool SafeId(string value) { return value != null && Regex.IsMatch(value, "\\A[A-Za-z0-9_-]{1,128}\\z"); }
        PortalReply Reply(int status, object body) { return new PortalReply { Status = status, Type = "application/json; charset=utf-8", Content = json.Serialize(body) }; }
        PortalReply Events(string events) { return new PortalReply { Status = 200, Type = "text/event-stream; charset=utf-8", Content = events }; }
        Dictionary<string, object> Body(string text, params string[] allowed)
        {
            if (String.IsNullOrEmpty(text) || Encoding.UTF8.GetByteCount(text) > 8 * 1024 * 1024) throw new ArgumentException("Invalid request size.");
            Dictionary<string, object> body;
            try { body = json.DeserializeObject(text) as Dictionary<string, object>; }
            catch (InvalidOperationException error) { throw new ArgumentException("Invalid request JSON.", error); }
            if (body == null || body.Keys.Any(key => !allowed.Contains(key))) throw new ArgumentException("Invalid request fields.");
            return body;
        }
        static string Identity(Dictionary<string, object> body, string key)
        {
            object value;
            if (!body.TryGetValue(key, out value) || !SafeId(value as string)) throw new ArgumentException("Invalid request identity.");
            return (string)value;
        }
        static List<Dictionary<string, string>> Messages(object value, int maximum, int byteLimit)
        {
            var input = value as object[];
            if (input == null || input.Length == 0 || input.Length > maximum) throw new ArgumentException("Invalid messages.");
            int bytes = 0;
            return input.Select(item =>
            {
                var row = item as Dictionary<string, object>;
                if (row == null || row.Count != 2 || !row.ContainsKey("role") || !row.ContainsKey("content") ||
                    !(row["role"] is string) || !(row["content"] is string)) throw new ArgumentException("Invalid message.");
                string role = (string)row["role"], content = (string)row["content"];
                if ((role != "user" && role != "assistant") || content.Length > 16384) throw new ArgumentException("Invalid message.");
                bytes += Encoding.UTF8.GetByteCount(content);
                if (bytes > byteLimit) throw new ArgumentException("Conversation is too large.");
                return new Dictionary<string, string> { { "role", role }, { "content", content } };
            }).ToList();
        }
        public async Task<PortalReply> HandleAsync(string documentOrigin, string requestOrigin, string method, string path, string content)
        {
            if (documentOrigin != Origin || requestOrigin != Origin) return Reply(403, new { detail = "Private Portal channel required." });
            if (disposed) return Reply(503, new { detail = "The native Portal has closed." });
            try
            {
                if (method == "GET" && path == "/api/pixel/status") return Reply(200, new { available = model.IsRunning,
                    state = model.IsRunning ? "available" : "unavailable", detail = model.IsRunning ? "" : "Use Restart local AI to recover the owned model.",
                    runtime = new { source = "external-host", model = modelName, contextLength = 4096 } });
                const string deletePrefix = "/api/pixel/images/";
                if (method == "DELETE" && path.StartsWith(deletePrefix, StringComparison.Ordinal) && SafeId(path.Substring(deletePrefix.Length)))
                {
                    string deletedChat = path.Substring(deletePrefix.Length);
                    lock (gate)
                    {
                        if (active != null && active.Chat == deletedChat) return Reply(409, new { detail = "Stop the active response before deleting this conversation." });
                        state.Delete(deletedChat);
                    }
                    return Reply(200, new { schemaVersion = 1, deleted = true });
                }
                if (method != "POST") return Reply(405, new { detail = "Method not supported." });
                if (path == "/api/pixel/chat/stream") return await BeginAsync(content);
                var body = Body(content, "chat_id", "request_id");
                string chat = Identity(body, "chat_id");
                if (path == "/api/pixel/chat/context") return Reply(200, new { schemaVersion = 1, status = "missing", context = (object)null,
                    compaction = new { status = "idle" }, history = new { acknowledgedMessages = 0, revision = (object)null, status = "ready" } });
                if (path == "/api/pixel/chat/result")
                {
                    lock (gate)
                    {
                        var result = state.Find(chat, Identity(body, "request_id"));
                        return result == null ? Reply(404, new { detail = "No retained result exists. No request was replayed." })
                            : Reply(200, new { state = result.State, events = result.Events });
                    }
                }
                if (path == "/api/pixel/chat/activity")
                { lock (gate) return Reply(200, new { state = active != null && active.Chat == chat ? "active" : "terminal" }); }
                if (path == "/api/pixel/chat/cancel") return await CancelAsync(chat, body.ContainsKey("request_id") ? Identity(body, "request_id") : null);
                return Reply(404, new { detail = "This capability is not available in the native text-chat preview." });
            }
            catch (PortalCapacityException error)
            {
                return method == "DELETE" ? Reply(507, new { detail = error.Message })
                    : Reply(507, new { detail = new { code = "native_storage_full", message = error.Message } });
            }
            catch (ArgumentException) { return Reply(422, new { detail = "Invalid native conversation request. No new response was started." }); }
        }
        async Task<PortalReply> BeginAsync(string content)
        {
            var body = Body(content, "chat_id", "request_id", "messages", "history_snapshot");
            string chat = Identity(body, "chat_id"), request = Identity(body, "request_id");
            object raw;
            if (!body.TryGetValue("messages", out raw)) throw new ArgumentException("Messages required.");
            var messages = Messages(raw, 50, 256 * 1024);
            if (messages.Last()["role"] != "user" || String.IsNullOrWhiteSpace(messages.Last()["content"])) throw new ArgumentException("User message required.");
            if (!body.TryGetValue("history_snapshot", out raw)) throw new ArgumentException("History required.");
            var snapshot = raw as Dictionary<string, object>;
            if (snapshot == null || snapshot.Count != 2 || !snapshot.ContainsKey("schemaVersion") || !Equals(snapshot["schemaVersion"], 1) || !snapshot.ContainsKey("messages"))
                throw new ArgumentException("Text history v1 required.");
            var history = Messages(snapshot["messages"], 2000, 4 * 1024 * 1024);
            if (history.Count < messages.Count || !history.Skip(history.Count - messages.Count).Zip(messages,
                (left, right) => left["role"] == right["role"] && left["content"] == right["content"]).All(match => match))
                throw new ArgumentException("History mismatch.");
            Task<PortalReply> operation;
            lock (gate)
            {
                if (state.IsDeleted(chat)) return Reply(410, new { detail = new { code = "conversation_deleted" } });
                string fingerprint = PortalState.Digest(content);
                var previous = state.Find(chat, request);
                if (previous != null)
                {
                    if (previous.Fingerprint != fingerprint) return Reply(409, new { detail = "The request identity already names a different response." });
                    if (previous.State != "active") return Events(previous.Events);
                    return Reply(409, new { detail = "The response is already active. Recover its saved result instead of submitting it again." });
                }
                if (active != null) return Reply(409, new { detail = "Another local response is active. Stop it or wait before sending." });
                if (!model.IsRunning) return Reply(503, new { detail = "Use Restart local AI before sending." });
                active = new PortalReceipt { Chat = chat, Request = request, Fingerprint = fingerprint, State = "active", Events = "" };
                try { state.Save(active); } catch { active = null; throw; }
                cancellation = new CancellationTokenSource();
                operation = running = RunAsync(active, messages, cancellation.Token);
            }
            return await operation;
        }
        async Task<PortalReply> RunAsync(PortalReceipt receipt, List<Dictionary<string, string>> messages, CancellationToken token)
        {
            // Yield before model work so admission publishes its exact task handle.
            await Task.Yield();
            string events = FailureEvents, terminal = "interrupted";
            Exception unexpected = null;
            try
            {
                string answer = await model.CompleteAsync(messages, token);
                token.ThrowIfCancellationRequested();
                events = "data: " + json.Serialize(new { choices = new[] { new { delta = new { content = answer }, finish_reason = "stop" } } }) + "\n\ndata: [DONE]\n\n";
                terminal = "complete";
            }
            catch (OperationCanceledException)
            {
                // A request timeout is distinct from an owner cancellation.
                if (token.IsCancellationRequested) { terminal = "cancelled"; events = ""; }
            }
            catch (HttpRequestException) { events = FailureEvents; }
            catch (NativeContextLimitException error)
            {
                events = "data: " + json.Serialize(new { error = new { type = "native_context_error", code = "native_context_limit", message = error.Message } }) + "\n\ndata: [DONE]\n\n";
            }
            catch (InvalidOperationException) { events = FailureEvents; }
            catch (InvalidDataException) { events = FailureEvents; }
            catch (Exception error) { unexpected = error; } // Record worker failure, then rethrow below.
            if (terminal != "complete")
            {
                try { await model.VerifyIdleAsync(); }
                catch (Exception)
                {
                    // Never admit another turn while an earlier inference may
                    // still run. Dispose owns only this runtime's retained Job.
                    try
                    {
                        model.Dispose();
                        if (model.IsRunning) throw new IOException("The owned model could not be confirmed stopped.");
                    }
                    catch (Exception error) { terminal = "interrupted"; events = FailureEvents; unexpected = error; }
                }
            }
            try
            {
                lock (gate)
                {
                    if (receipt.State == "active")
                    {
                        receipt.State = terminal; receipt.Events = events;
                        state.Save(receipt);
                    }
                }
            }
            finally
            {
                lock (gate) if (active == receipt) { active = null; cancellation.Dispose(); cancellation = null; running = null; }
            }
            if (unexpected != null) ExceptionDispatchInfo.Capture(unexpected).Throw();
            return Events(receipt.Events);
        }
        async Task<PortalReply> CancelAsync(string chat, string request)
        {
            Task<PortalReply> operation;
            PortalReceipt target;
            lock (gate)
            {
                if (active == null)
                {
                    var prior = request == null ? null : state.Find(chat, request);
                    return prior != null ? Reply(200, new { aborted = prior.State != "complete" }) : Reply(404, new { detail = "No matching response exists." });
                }
                if (active.Chat != chat || request != null && active.Request != request)
                    return Reply(409, new { detail = "The cancellation identity does not own the active response." });
                target = active; operation = running; cancellation.Cancel();
            }
            await operation;
            // Completion can win the race with Stop. Let the existing reader
            // deliver that saved answer instead of replacing it with a notice.
            lock (gate) return Reply(200, new { aborted = target.State != "complete" });
        }
        public void Dispose()
        {
            lock (gate)
            {
                disposed = true;
                try
                {
                    if (active != null) { active.State = "interrupted"; active.Events = FailureEvents; state.Save(active); }
                }
                finally { if (cancellation != null) cancellation.Cancel(); }
            }
        }
    }
}
