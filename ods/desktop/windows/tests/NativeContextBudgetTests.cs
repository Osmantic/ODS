// SPDX-License-Identifier: Apache-2.0
// Real context-budget code over a fake authenticated HTTP pipeline; no model work.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using OdsDesktop;

static class NativeContextBudgetTests
{
    static int checks;
    static void Check(bool value, string message)
    { if (!value) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static Dictionary<string, string> Message(string role, string content)
    { return new Dictionary<string, string> { { "role", role }, { "content", content } }; }
    static List<Dictionary<string, string>> History(int pairs)
    {
        var messages = new List<Dictionary<string, string>>();
        for (int i = 0; i < pairs; i++) { messages.Add(Message("user", "question " + i)); messages.Add(Message("assistant", "answer " + i)); }
        messages.Add(Message("user", "latest user message"));
        return messages;
    }

    sealed class Pipeline : HttpMessageHandler
    {
        readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
        public Func<int, int> Count = count => 100 + count * 250;
        public string BadTemplate, BadTokens;
        public bool HoldTemplate, HoldTokens;
        public HttpStatusCode Status = HttpStatusCode.OK;
        public int Calls, Templates, Tokenizations;
        public readonly TaskCompletionSource<bool> Held = new TaskCompletionSource<bool>();
        public readonly List<string[]> Seen = new List<string[]>();
        string prompt;
        int promptMessages;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Calls++;
            if (request.Method != HttpMethod.Post || request.Headers.Authorization == null ||
                request.Headers.Authorization.Scheme != "Bearer" || request.Headers.Authorization.Parameter != "context-fixture")
                throw new Exception("Context checks must reuse the supplied authenticated client.");
            var body = (Dictionary<string, object>)json.DeserializeObject(await request.Content.ReadAsStringAsync());
            string response;
            if (request.RequestUri.AbsolutePath == "/apply-template")
            {
                Templates++;
                var kwargs = body["chat_template_kwargs"] as Dictionary<string, object>;
                if (body.Count != 5 || !Equals(body["add_generation_prompt"], true) || !Equals(body["stream"], true) ||
                    !Equals(body["max_tokens"], 512) || kwargs == null || kwargs.Count != 1 || !Equals(kwargs["enable_thinking"], false))
                    throw new Exception("Template options disagree with the shipped chat-completions producer.");
                var messages = (object[])body["messages"];
                Seen.Add(messages.Select(row => (string)((Dictionary<string, object>)row)["role"] + ":" + (string)((Dictionary<string, object>)row)["content"]).ToArray());
                promptMessages = messages.Length;
                prompt = "rendered-special-tokens:" + json.Serialize(messages);
                if (HoldTemplate) { Held.TrySetResult(true); await Task.Delay(Timeout.Infinite, token); }
                response = BadTemplate ?? json.Serialize(new { prompt });
            }
            else if (request.RequestUri.AbsolutePath == "/tokenize")
            {
                Tokenizations++;
                if (body.Count != 4 || !Equals(body["content"], prompt) || !Equals(body["add_special"], true) ||
                    !Equals(body["parse_special"], true) || !Equals(body["with_pieces"], false))
                    throw new Exception("Tokenization must count the exact rendered prompt with both completion flags true.");
                if (HoldTokens) { Held.TrySetResult(true); await Task.Delay(Timeout.Infinite, token); }
                response = BadTokens ?? json.Serialize(new { tokens = Enumerable.Range(0, Count(promptMessages)).ToArray() });
            }
            else throw new Exception("Budget checks must never call inference, tools or another route.");
            return new HttpResponseMessage(Status) { Content = new StringContent(response, Encoding.UTF8, "application/json") };
        }
        public HttpClient Client()
        {
            var client = new HttpClient(this, false) { BaseAddress = new Uri("http://127.0.0.1:1/") };
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", "context-fixture");
            return client;
        }
    }

    static async Task RefusesMalformed(string template, string tokens)
    {
        using (var pipeline = new Pipeline { BadTemplate = template, BadTokens = tokens })
        using (var client = pipeline.Client())
        {
            bool refused = false;
            try { await NativeContextBudget.FitAsync(client, History(1), CancellationToken.None); }
            catch (InvalidDataException) { refused = true; }
            Check(refused && pipeline.Calls == (template == null ? 2 : 1), "malformed context response refuses without inference or fallback estimation");
        }
    }

    public static async Task<int> Run()
    {
        checks = 0;
        using (var pipeline = new Pipeline())
        using (var client = pipeline.Client())
        {
            var history = History(10);
            string original = new JavaScriptSerializer().Serialize(history);
            var fitted = await NativeContextBudget.FitAsync(client, history, CancellationToken.None);
            Check(fitted.Count == 13 && fitted[0]["content"] == "question 4" && fitted.Last()["content"] == "latest user message",
                "long chat retains the newest complete turns and the entire latest user message");
            Check(pipeline.Seen.Select(row => row.Length).SequenceEqual(new[] { 21, 19, 17, 15, 13 }),
                "each token-budget retry removes exactly the oldest whole question/answer turn");
            Check(pipeline.Templates == 5 && pipeline.Tokenizations == 5, "every candidate uses the real template then tokenizer contract");
            Check(new JavaScriptSerializer().Serialize(history) == original, "trimming leaves the original full conversation unchanged");
            fitted[0]["content"] = "modified copy";
            Check(new JavaScriptSerializer().Serialize(history) == original, "selected messages have independent dictionaries");
        }
        foreach (int count in new[] { 3583, 3584, 3585 })
        {
            using (var pipeline = new Pipeline { Count = ignored => count })
            using (var client = pipeline.Client())
            {
                bool refused = false;
                try { await NativeContextBudget.FitAsync(client, History(0), CancellationToken.None); }
                catch (NativeContextLimitException error) { refused = error.Message.Contains("Shorten") && error.Message.Contains("new chat"); }
                Check(refused == (count > 3584) && pipeline.Calls == 2, "4096-token context reserves exactly 512 output tokens and gives actionable single-prompt refusal");
            }
        }
        using (var pipeline = new Pipeline { Count = count => count > 1 ? 4000 : 50 })
        using (var client = pipeline.Client())
        {
            var history = new List<Dictionary<string, string>> {
                Message("user", "old question"), Message("assistant", "answer fragment one"),
                Message("assistant", "answer fragment two"), Message("user", "latest unchanged") };
            var fitted = await NativeContextBudget.FitAsync(client, history, CancellationToken.None);
            Check(fitted.Count == 1 && fitted[0]["content"] == "latest unchanged" && history.Count == 4,
                "all assistant fragments leave with their old user turn; the latest prompt is never sliced");
        }
        foreach (string body in new[] { "{", "[]", "{}", "{\"prompt\":7}", "{\"prompt\":\"\"}", "{\"prompt\":\"x\",\"error\":\"bad\"}" })
            await RefusesMalformed(body, null);
        foreach (string body in new[] { "{", "{}", "{\"tokens\":\"1\"}", "{\"tokens\":[]}", "{\"tokens\":[1,\"2\"]}",
            "{\"tokens\":[-1]}", "{\"tokens\":[1.5]}", "{\"tokens\":[1],\"error\":\"bad\"}", new string(' ', 4 * 1024 * 1024 + 1) })
            await RefusesMalformed(null, body);
        using (var pipeline = new Pipeline { Status = HttpStatusCode.Unauthorized })
        using (var client = pipeline.Client())
        {
            bool refused = false;
            try { await NativeContextBudget.FitAsync(client, History(1), CancellationToken.None); }
            catch (HttpRequestException) { refused = true; }
            Check(refused && pipeline.Calls == 1, "failed authenticated template requests are not treated as usable token counts");
        }
        using (var pipeline = new Pipeline())
        using (var client = pipeline.Client())
        using (var cancellation = new CancellationTokenSource())
        {
            cancellation.Cancel(); bool cancelled = false;
            try { await NativeContextBudget.FitAsync(client, History(1), cancellation.Token); }
            catch (OperationCanceledException) { cancelled = true; }
            Check(cancelled && pipeline.Calls == 0, "pre-cancelled context requests make no HTTP calls");
        }
        foreach (bool holdTemplate in new[] { true, false })
        {
            using (var pipeline = new Pipeline { HoldTemplate = holdTemplate, HoldTokens = !holdTemplate })
            using (var client = pipeline.Client())
            using (var cancellation = new CancellationTokenSource())
            {
                var pending = NativeContextBudget.FitAsync(client, History(1), cancellation.Token);
                await pipeline.Held.Task;
                cancellation.Cancel(); bool cancelled = false;
                try { await pending; } catch (OperationCanceledException) { cancelled = true; }
                Check(cancelled && pipeline.Calls == (holdTemplate ? 1 : 2), "Stop cancels the owned template/tokenizer request before any inference");
            }
        }
        using (var pipeline = new Pipeline { HoldTokens = true })
        using (var client = pipeline.Client())
        {
            var clock = Stopwatch.StartNew(); bool timedOut = false;
            try { await NativeContextBudget.FitAsync(client, History(1), CancellationToken.None, TimeSpan.FromMilliseconds(100)); }
            catch (OperationCanceledException) { timedOut = true; }
            Check(timedOut && clock.Elapsed < TimeSpan.FromSeconds(5) && pipeline.Calls == 2,
                "one bounded deadline covers the whole context-selection operation");
        }
        return checks;
    }
}
