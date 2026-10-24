// SPDX-License-Identifier: Apache-2.0
// b9014 contract checks over fake authenticated HTTP; never invokes inference.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using OdsDesktop;

static class NativeDocumentBudgetTests
{
    static int checks;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 4 * 1024 * 1024, RecursionLimit = 64 };
    static void Check(bool value, string message)
    { if (!value) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static Dictionary<string, object> Row(string role, object content)
    { return new Dictionary<string, object> { { "role", role }, { "content", content } }; }
    static Dictionary<string, object> ToolCall(string id)
    {
        return new Dictionary<string, object> {
            { "id", id }, { "type", "function" }, { "function", new Dictionary<string, object> {
                { "name", "create_markdown" }, { "arguments", "{\"filename\":\"document.md\",\"markdown\":\"# Owner document\"}" } } }
        };
    }
    static object[] ToolGroup(string label)
    {
        var call = Row("assistant", null);
        call.Add("tool_calls", new object[] { ToolCall(label + "_call") });
        var result = Row("tool", "{\"status\":\"staged\",\"filename\":\"document.md\",\"bytes\":16,\"sha256\":\"fixture-digest\"}");
        result.Add("tool_call_id", label + "_call");
        return new object[] { Row("user", label), call, result };
    }
    static Dictionary<string, object> Request(object[] messages, bool final)
    {
        // Actual native producer fields, including the tool schema; these also
        // feed oaicompat_chat_params_parse in /apply-template on pinned b9014.
        return new Dictionary<string, object> {
            { "model", "ods-desktop-preview" }, { "messages", messages },
            { "tools", new object[] { new Dictionary<string, object> {
                { "type", "function" }, { "function", new Dictionary<string, object> {
                    { "name", "create_markdown" }, { "description", "Create one short Markdown document requested by the owner. The native host validates and stages its bytes; it publishes only when this turn completes." },
                    { "parameters", new Dictionary<string, object> {
                        { "type", "object" }, { "additionalProperties", false },
                        { "required", new[] { "filename", "markdown" } },
                        { "properties", new Dictionary<string, object> {
                            { "filename", new Dictionary<string, object> { { "type", "string" }, { "enum", new[] { "document.md" } } } },
                            { "markdown", new Dictionary<string, object> { { "type", "string" }, { "minLength", 1 }, { "maxLength", 65536 } } }
                        } }
                    } }
                } }
            } } },
            { "tool_choice", final ? (object)"none" : new Dictionary<string, object> {
                { "type", "function" }, { "function", new Dictionary<string, object> { { "name", "create_markdown" } } }
            } },
            { "parallel_tool_calls", false }, { "stream", false }, { "max_tokens", final ? 128 : 1024 },
            { "temperature", 0.2 }, { "chat_template_kwargs", new Dictionary<string, object> { { "enable_thinking", false } } }
        };
    }
    static string Canonical(object value)
    {
        var map = value as Dictionary<string, object>;
        if (map != null) return "{" + String.Join(",", map.Keys.OrderBy(key => key, StringComparer.Ordinal)
            .Select(key => Json.Serialize(key) + ":" + Canonical(map[key]))) + "}";
        var rows = value as System.Collections.IEnumerable;
        if (rows != null && !(value is string)) return "[" + String.Join(",", rows.Cast<object>().Select(Canonical)) + "]";
        return Json.Serialize(value);
    }
    static object Clone(object value) { return Json.DeserializeObject(Json.Serialize(value)); }

    sealed class Pipeline : HttpMessageHandler
    {
        readonly Dictionary<string, object> expected;
        public Func<int, int> Count = ignored => 100;
        public bool HoldTemplate, HoldTokens;
        public int Calls, Templates, Tokenizations;
        public readonly List<object[]> Seen = new List<object[]>();
        public readonly TaskCompletionSource<bool> Held = new TaskCompletionSource<bool>();
        string prompt;
        int rows;
        public Pipeline(Dictionary<string, object> request)
        { expected = (Dictionary<string, object>)Clone(request); expected.Remove("messages"); }
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Calls++;
            if (request.Method != HttpMethod.Post || request.Headers.Authorization == null ||
                request.Headers.Authorization.Scheme != "Bearer" || request.Headers.Authorization.Parameter != "document-budget-fixture")
                throw new Exception("Document budget must reuse the supplied authenticated client.");
            var body = (Dictionary<string, object>)Json.DeserializeObject(await request.Content.ReadAsStringAsync());
            string response;
            if (request.RequestUri.AbsolutePath == "/apply-template")
            {
                Templates++;
                if (!body.ContainsKey("add_generation_prompt") || !Equals(body["add_generation_prompt"], true))
                    throw new Exception("The template must add the assistant generation prompt, as b9014 completions do.");
                body.Remove("add_generation_prompt");
                var messages = (object[])body["messages"];
                Seen.Add(messages); rows = messages.Length; body.Remove("messages");
                if (Canonical(body) != Canonical(expected))
                    throw new Exception("Tools, tool_choice, actual output limit, parallel flag and template kwargs must reach rendering unchanged.");
                // The tokenizer must receive this exact rendered value rather
                // than message text or a separate token estimate.
                prompt = "<|im_start|>rendered:" + Json.Serialize(messages) + "<|im_start|>assistant";
                if (HoldTemplate) { Held.TrySetResult(true); await Task.Delay(Timeout.Infinite, token); }
                response = Json.Serialize(new { prompt });
            }
            else if (request.RequestUri.AbsolutePath == "/tokenize")
            {
                Tokenizations++;
                // server-context.cpp handle_completions_impl passes true,true;
                // /tokenize itself defaults add_special to false on b9014.
                if (body.Count != 4 || !Equals(body["content"], prompt) || !Equals(body["add_special"], true) ||
                    !Equals(body["parse_special"], true) || !Equals(body["with_pieces"], false))
                    throw new Exception("Document budgeting must tokenize the rendered prompt with completion flags true,true.");
                if (HoldTokens) { Held.TrySetResult(true); await Task.Delay(Timeout.Infinite, token); }
                response = Json.Serialize(new { tokens = Enumerable.Range(0, Count(rows)).ToArray() });
            }
            else throw new Exception("Budgeting must never invoke inference, tools, files or another HTTP route.");
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(response, Encoding.UTF8, "application/json") };
        }
        public HttpClient Client()
        {
            var client = new HttpClient(this, false) { BaseAddress = new Uri("http://127.0.0.1:1/") };
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", "document-budget-fixture");
            return client;
        }
    }

    public static async Task<int> Run()
    {
        checks = 0;
        foreach (bool final in new[] { false, true })
        {
            // Real producer limits: first tool generation leaves 384 tokens for
            // its follow-up; the final response reserves its own 128 output.
            int limit = final ? 3968 : 2688;
            foreach (int count in new[] { limit - 1, limit, limit + 1 })
            {
                var request = Request(final ? ToolGroup("latest owner request") : new object[] { Row("user", "latest owner request") }, final);
                string original = Canonical(Clone(request));
                using (var pipeline = new Pipeline(request) { Count = ignored => count })
                using (var client = pipeline.Client())
                {
                    bool refused = false;
                    Dictionary<string, object> result = null;
                    try { result = await NativeContextBudget.FitRequestAsync(client, request, final ? 0 : 384, CancellationToken.None); }
                    catch (NativeContextLimitException error) { refused = error.Message.Contains("Shorten") && error.Message.Contains("new chat"); }
                    Check(refused == (count > limit) && pipeline.Calls == 2,
                        (final ? "final 128-token answer" : "1024-token tool output plus 384-token follow-up") + " enforces exact prompt boundary " + count);
                    Check(Canonical(Clone(request)) == original, "fitting or refusal leaves the full saved input and nested producer options unchanged");
                    if (!refused) Check(Canonical(result) == original && pipeline.Templates == 1 && pipeline.Tokenizations == 1,
                        "fitted request keeps the actual producer payload for later inference");
                }
            }
        }
        var olderTool = ToolGroup("older tool owner").Concat(new object[] { Row("assistant", "older completed summary") });
        var history = olderTool.Concat(new object[] { Row("user", "older ordinary owner"), Row("assistant", "older ordinary answer") })
            .Concat(ToolGroup("current document owner")).ToArray();
        var longRequest = Request(history, true);
        string fullInput = Canonical(Clone(longRequest));
        using (var pipeline = new Pipeline(longRequest) { Count = count => count == 9 ? 5000 : count == 5 ? 4100 : 3968 })
        using (var client = pipeline.Client())
        {
            var fitted = await NativeContextBudget.FitRequestAsync(client, longRequest, 0, CancellationToken.None);
            Check(pipeline.Seen.Select(rows => rows.Length).SequenceEqual(new[] { 9, 5, 3 }),
                "overflow removes complete older user-led groups including their tool calls, results and summaries");
            Check(Canonical(pipeline.Seen[1]) == Canonical(history.Skip(4).ToArray()) &&
                Canonical(pipeline.Seen[2]) == Canonical(history.Skip(6).ToArray()),
                "each retry preserves all roles, call identities and tool result bytes of the remaining suffix");
            Check(Canonical(fitted["messages"]) == Canonical(ToolGroup("current document owner")),
                "the latest owner prompt, assistant tool call and staged tool result stay together");
            Check(Canonical(Clone(longRequest)) == fullInput && pipeline.Templates == 3 && pipeline.Tokenizations == 3,
                "each reduced candidate is rendered and tokenized without changing full saved history");
            var savedRows = (object[])fitted["messages"];
            ((Dictionary<string, object>)savedRows[0])["content"] = "caller changed fitted prompt";
            var calls = (object[])((Dictionary<string, object>)savedRows[1])["tool_calls"];
            ((Dictionary<string, object>)((Dictionary<string, object>)calls[0])["function"])["arguments"] = "caller changed tool arguments";
            var tools = (object[])fitted["tools"];
            ((Dictionary<string, object>)((Dictionary<string, object>)tools[0])["function"])["name"] = "caller changed schema";
            Check(Canonical(Clone(longRequest)) == fullInput, "returned messages, tool call arguments and tool schema are deep independent copies");
        }
        var cancelledRequest = Request(ToolGroup("current document owner"), true);
        using (var pipeline = new Pipeline(cancelledRequest))
        using (var client = pipeline.Client())
        using (var cancellation = new CancellationTokenSource())
        {
            cancellation.Cancel(); bool cancelled = false;
            try { await NativeContextBudget.FitRequestAsync(client, cancelledRequest, 0, cancellation.Token); }
            catch (OperationCanceledException) { cancelled = true; }
            Check(cancelled && pipeline.Calls == 0, "pre-cancelled document budgets perform no HTTP or inference");
        }
        foreach (bool holdTemplate in new[] { true, false })
        {
            using (var pipeline = new Pipeline(cancelledRequest) { HoldTemplate = holdTemplate, HoldTokens = !holdTemplate })
            using (var client = pipeline.Client())
            using (var cancellation = new CancellationTokenSource())
            {
                var pending = NativeContextBudget.FitRequestAsync(client, cancelledRequest, 0, cancellation.Token);
                var reached = await Task.WhenAny(pipeline.Held.Task, Task.Delay(3000));
                if (reached != pipeline.Held.Task) { cancellation.Cancel(); throw new Exception("Budget did not reach expected cancellable HTTP stage."); }
                cancellation.Cancel(); bool cancelled = false;
                try { await pending; } catch (OperationCanceledException) { cancelled = true; }
                Check(cancelled && pipeline.Calls == (holdTemplate ? 1 : 2), "Stop interrupts document template or tokenization before inference");
            }
        }
        return checks;
    }
}
