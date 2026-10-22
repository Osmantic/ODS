// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace OdsDesktop
{
    public sealed class NativeContextLimitException : InvalidOperationException
    {
        public NativeContextLimitException()
            : base("This message is too large for the local AI context. Shorten the message or start a new chat. Your saved conversation was kept.") { }
    }

    public static class NativeContextBudget
    {
        public const int ContextTokens = 4096;
        public const int OutputTokens = 512;
        public const int PromptTokens = ContextTokens - OutputTokens;
        const int MaximumResponseBytes = 4 * 1024 * 1024;

        public static Task<List<Dictionary<string, string>>> FitAsync(HttpClient client,
            List<Dictionary<string, string>> messages, CancellationToken token)
        { return FitAsync(client, messages, token, TimeSpan.FromSeconds(30)); }

        internal static async Task<List<Dictionary<string, string>>> FitAsync(HttpClient client,
            List<Dictionary<string, string>> messages, CancellationToken token, TimeSpan timeout)
        {
            if (client == null) throw new ArgumentNullException("client");
            if (timeout <= TimeSpan.Zero || timeout > TimeSpan.FromSeconds(30)) throw new ArgumentOutOfRangeException("timeout");
            token.ThrowIfCancellationRequested();
            var selected = Snapshot(messages);
            var json = new JavaScriptSerializer { MaxJsonLength = MaximumResponseBytes, RecursionLimit = 32 };
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(token))
            {
                deadline.CancelAfter(timeout);
                while (true)
                {
                    deadline.Token.ThrowIfCancellationRequested();
                    // b9014 server-context.cpp: post_apply_template uses the same
                    // oaicompat_chat_params_parse as post_chat_completions. Keep
                    // these template arguments equal to the inference producer.
                    var rendered = await PostAsync(client, "apply-template", new
                    {
                        messages = selected, stream = true, max_tokens = OutputTokens,
                        add_generation_prompt = true, chat_template_kwargs = new { enable_thinking = false }
                    }, json, deadline.Token).ConfigureAwait(false);
                    object raw;
                    if (rendered.Count != 1 || !rendered.TryGetValue("prompt", out raw) ||
                        !(raw is string) || String.IsNullOrEmpty((string)raw))
                        throw new InvalidDataException("The local model returned an invalid rendered prompt. No inference was started.");
                    // handle_completions_impl tokenizes its rendered prompt with
                    // both flags true; /tokenize defaults add_special to false.
                    var tokenized = await PostAsync(client, "tokenize", new
                    {
                        content = (string)raw, add_special = true, parse_special = true, with_pieces = false
                    }, json, deadline.Token).ConfigureAwait(false);
                    object tokens;
                    if (tokenized.Count != 1 || !tokenized.TryGetValue("tokens", out tokens) || !(tokens is object[]))
                        throw new InvalidDataException("The local model returned an invalid token count. No inference was started.");
                    var ids = (object[])tokens;
                    if (ids.Length == 0 || ids.Any(id => !(id is int) || (int)id < 0))
                        throw new InvalidDataException("The local model returned invalid token identities. No inference was started.");
                    deadline.Token.ThrowIfCancellationRequested();
                    if (ids.Length <= PromptTokens) return selected;

                    // A turn starts with a user message and includes all following
                    // assistant messages. Drop its whole prefix, never half of a
                    // question/answer pair or any part of the latest user prompt.
                    int nextUser = selected.FindIndex(1, message => message["role"] == "user");
                    if (nextUser < 0) throw new NativeContextLimitException();
                    selected.RemoveRange(0, nextUser);
                }
            }
        }

        static List<Dictionary<string, string>> Snapshot(List<Dictionary<string, string>> messages)
        {
            if (messages == null || messages.Count == 0 || messages.Count > 50) throw new ArgumentException("Invalid native context messages.");
            int bytes = 0;
            var result = new List<Dictionary<string, string>>(messages.Count);
            foreach (var message in messages)
            {
                string role, content;
                if (message == null || message.Count != 2 || !message.TryGetValue("role", out role) ||
                    !message.TryGetValue("content", out content) || (role != "user" && role != "assistant") ||
                    content == null || content.Length > 16384)
                    throw new ArgumentException("Invalid native context message.");
                bytes += Encoding.UTF8.GetByteCount(content);
                if (bytes > 256 * 1024) throw new ArgumentException("Native context input is too large.");
                result.Add(new Dictionary<string, string> { { "role", role }, { "content", content } });
            }
            if (result[0]["role"] != "user" || result[result.Count - 1]["role"] != "user" ||
                String.IsNullOrWhiteSpace(result[result.Count - 1]["content"]))
                throw new ArgumentException("Native context must start and end with a user message.");
            return result;
        }

        static async Task<Dictionary<string, object>> PostAsync(HttpClient client, string path, object body,
            JavaScriptSerializer json, CancellationToken token)
        {
            using (var request = new HttpRequestMessage(HttpMethod.Post, path))
            {
                request.Content = new StringContent(json.Serialize(body), Encoding.UTF8, "application/json");
                using (var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token).ConfigureAwait(false))
                {
                    response.EnsureSuccessStatusCode();
                    if (response.Content == null || response.Content.Headers.ContentType == null ||
                        response.Content.Headers.ContentType.MediaType != "application/json" ||
                        response.Content.Headers.ContentLength > MaximumResponseBytes)
                        throw new InvalidDataException("The local context service returned an invalid response. No inference was started.");
                    // ResponseHeadersRead avoids unbounded buffering. Disposing
                    // the owned response also interrupts Framework network reads
                    // if cancellation arrives after response headers.
                    using (token.Register(response.Dispose))
                    {
                        try
                        {
                            using (var input = await response.Content.ReadAsStreamAsync().ConfigureAwait(false))
                            using (var output = new MemoryStream())
                            {
                                var buffer = new byte[8192];
                                int read;
                                while ((read = await input.ReadAsync(buffer, 0, buffer.Length, token).ConfigureAwait(false)) > 0)
                                {
                                    if (output.Length + read > MaximumResponseBytes)
                                        throw new InvalidDataException("The local context response exceeded its limit. No inference was started.");
                                    output.Write(buffer, 0, read);
                                }
                                token.ThrowIfCancellationRequested();
                                Dictionary<string, object> parsed;
                                try { parsed = json.DeserializeObject(new UTF8Encoding(false, true).GetString(output.ToArray())) as Dictionary<string, object>; }
                                catch (ArgumentException error) { throw new InvalidDataException("The local context response was malformed JSON.", error); }
                                catch (InvalidOperationException error) { throw new InvalidDataException("The local context response was malformed JSON.", error); }
                                if (parsed == null) throw new InvalidDataException("The local context response was not an object.");
                                return parsed;
                            }
                        }
                        catch (IOException) { token.ThrowIfCancellationRequested(); throw; }
                        catch (ObjectDisposedException) { token.ThrowIfCancellationRequested(); throw; }
                    }
                }
            }
        }
    }
}
