// SPDX-License-Identifier: Apache-2.0
// One explicitly admitted, bounded document tool; no shell or host paths.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace OdsDesktop
{
    public interface INativeDocumentModel : IPortalModel
    {
        Task<NativeDocumentReply> CreateDocumentAsync(List<Dictionary<string, string>> messages, CancellationToken token);
    }
    public sealed class NativeDocumentReply
    {
        public readonly string Summary;
        public readonly NativeDocument Document;
        public NativeDocumentReply(string summary, NativeDocument document)
        {
            if (String.IsNullOrWhiteSpace(summary) || summary.Length > 8192 || document == null) throw new ArgumentException("Invalid document result.");
            Summary = summary; Document = document;
        }
    }
    public sealed partial class LocalRuntime : INativeDocumentModel
    {
        const string DocumentTool = "create_markdown";
        static object[] DocumentTools()
        {
            return new object[] { new { type = "function", function = new { name = DocumentTool,
                description = "Create one short Markdown document requested by the owner. The native host validates and stages its bytes; it publishes only when this turn completes.",
                parameters = new { type = "object", properties = new {
                    filename = new { type = "string", @enum = new[] { NativeDocument.FileName } },
                    markdown = new { type = "string", minLength = 1, maxLength = NativeDocument.MaximumBytes }
                }, required = new[] { "filename", "markdown" }, additionalProperties = false } } } };
        }
        static Dictionary<string, object> DocumentRequest(object messages, object choice, int outputTokens)
        {
            return new Dictionary<string, object> { { "model", Alias }, { "messages", messages }, { "tools", DocumentTools() },
                { "tool_choice", choice }, { "parallel_tool_calls", false }, { "stream", false }, { "max_tokens", outputTokens },
                { "temperature", 0.2 }, { "chat_template_kwargs", new { enable_thinking = false } } };
        }
        static Dictionary<string, object> DocumentChoice(Dictionary<string, object> response, string finish)
        {
            object raw;
            var choices = response != null && !response.ContainsKey("error") && response.TryGetValue("choices", out raw) ? raw as object[] : null;
            var choice = choices != null && choices.Length == 1 ? choices[0] as Dictionary<string, object> : null;
            var message = choice != null && choice.ContainsKey("message") ? choice["message"] as Dictionary<string, object> : null;
            if (choice == null || !choice.ContainsKey("finish_reason") || !Equals(choice["finish_reason"], finish) ||
                message == null || !message.ContainsKey("role") || !Equals(message["role"], "assistant"))
                throw new InvalidDataException("The local model did not complete the document tool turn.");
            return message;
        }
        public async Task<NativeDocumentReply> CreateDocumentAsync(List<Dictionary<string, string>> messages, CancellationToken token)
        {
            if (!IsRunning || client == null) throw new InvalidOperationException("Start local AI before creating a document.");
            if (messages == null || messages.Count == 0) throw new ArgumentException("A document request is required.");
            var history = messages.Select(message => (object)new Dictionary<string, object> { { "role", message["role"] }, { "content", message["content"] } }).ToArray();
            var serializer = new JavaScriptSerializer { MaxJsonLength = 4 * 1024 * 1024, RecursionLimit = 64 };
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(token))
            {
                deadline.CancelAfter(TimeSpan.FromMinutes(5));
                var first = await NativeContextBudget.FitRequestAsync(client,
                    DocumentRequest(history, new { type = "function", function = new { name = DocumentTool } }, 1024), 384, deadline.Token).ConfigureAwait(false);
                var message = DocumentChoice(await NativeContextBudget.PostAsync(client, "v1/chat/completions", first, serializer, deadline.Token).ConfigureAwait(false), "tool_calls");
                object raw;
                var calls = message.TryGetValue("tool_calls", out raw) ? raw as object[] : null;
                var call = calls != null && calls.Length == 1 ? calls[0] as Dictionary<string, object> : null;
                var function = call != null && call.ContainsKey("function") ? call["function"] as Dictionary<string, object> : null;
                if (call == null || call.Count != 3 || !call.ContainsKey("type") || !Equals(call["type"], "function") ||
                    !call.ContainsKey("id") || !(call["id"] is string) || !Regex.IsMatch((string)call["id"], "\\A[A-Za-z0-9_-]{1,128}\\z") ||
                    function == null || function.Count != 2 || !function.ContainsKey("name") || !Equals(function["name"], DocumentTool) ||
                    !function.ContainsKey("arguments") || !(function["arguments"] is string) || ((string)function["arguments"]).Length > 128 * 1024)
                    throw new InvalidDataException("The local model returned an unsupported document tool call.");
                Dictionary<string, object> arguments;
                try { arguments = serializer.DeserializeObject((string)function["arguments"]) as Dictionary<string, object>; }
                catch (ArgumentException error) { throw new InvalidDataException("The document tool arguments were invalid.", error); }
                catch (InvalidOperationException error) { throw new InvalidDataException("The document tool arguments were invalid.", error); }
                if (arguments == null || arguments.Count != 2 || !arguments.ContainsKey("filename") || !Equals(arguments["filename"], NativeDocument.FileName) ||
                    !arguments.ContainsKey("markdown") || !(arguments["markdown"] is string)) throw new InvalidDataException("Invalid document tool fields.");
                // Preparing creates an immutable private draft only. Neither the
                // model nor this method can grant a public download authority.
                NativeDocument document;
                try { document = NativeDocument.Prepare((string)arguments["markdown"]); }
                catch (ArgumentException error) { throw new InvalidDataException("The document content was invalid.", error); }
                deadline.Token.ThrowIfCancellationRequested();
                var next = new List<object>((object[])first["messages"]);
                next.Add(new { role = "assistant", content = (string)null, tool_calls = new[] { new {
                    id = (string)call["id"], type = "function", function = new { name = DocumentTool, arguments = (string)function["arguments"] } } } });
                next.Add(new { role = "tool", tool_call_id = (string)call["id"], content = serializer.Serialize(new {
                    status = "staged", filename = NativeDocument.FileName, bytes = document.ReadBytes().Length, sha256 = document.FileSha256,
                    instruction = "The validated document will be published with your completed reply. Briefly tell the owner to use the document download card. Do not invent paths or URLs."
                }) });
                var final = await NativeContextBudget.FitRequestAsync(client, DocumentRequest(next.ToArray(), "none", 128), 0, deadline.Token).ConfigureAwait(false);
                var answer = DocumentChoice(await NativeContextBudget.PostAsync(client, "v1/chat/completions", final, serializer, deadline.Token).ConfigureAwait(false), "stop");
                if (answer.ContainsKey("tool_calls") && answer["tool_calls"] != null && (!(answer["tool_calls"] is object[]) || ((object[])answer["tool_calls"]).Length != 0))
                    throw new InvalidDataException("The final document reply attempted another tool call.");
                string summary = answer.ContainsKey("content") ? answer["content"] as string : null;
                if (String.IsNullOrWhiteSpace(summary) || summary.Length > 8192)
                    throw new InvalidDataException("The local model did not finish its document reply.");
                deadline.Token.ThrowIfCancellationRequested();
                return new NativeDocumentReply(summary, document);
            }
        }
    }
}
