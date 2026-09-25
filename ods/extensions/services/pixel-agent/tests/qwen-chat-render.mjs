// Test-only JavaScript rendering of the Qwen3.5 chat template that ODS ships in
// ods/config/llama-server/templates/, applied the way llama.cpp's server
// applies it to an OpenAI chat request (null content becomes "", tool-call
// argument strings become objects). It covers the message shapes Pixel sends:
// one system message, text user messages, assistant text and tool calls, and
// tool results. tests/chat_template_prefix.integration.mjs checks it byte for
// byte against the pinned llama.cpp engine rendering the shipped file.

const TOOL_INSTRUCTIONS = '\n\nIf you choose to call a function ONLY reply in the following format with NO suffix:\n\n<tool_call>\n<function=example_function_name>\n<parameter=example_parameter_1>\nvalue_1\n</parameter>\n<parameter=example_parameter_2>\nThis is the value for the second parameter\nthat can span\nmultiple lines\n</parameter>\n</function>\n</tool_call>\n\n<IMPORTANT>\nReminder:\n- Function calls MUST follow the specified format: an inner <function=...></function> block must be nested within <tool_call></tool_call> XML tags\n- Required parameters MUST be specified\n- You may provide optional reasoning for your function call in natural language BEFORE the function call, but NOT after\n- If there is no function call available, answer the question like normal with your current knowledge and do not tell the user about function calls\n</IMPORTANT>';

// Jinja's tojson in llama.cpp: insertion order, ", " and ": " separators.
export function tojson(value) {
  if (Array.isArray(value)) return `[${value.map(tojson).join(', ')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.entries(value).map(([key, item]) => `${JSON.stringify(key)}: ${tojson(item)}`).join(', ')}}`;
  }
  return JSON.stringify(value);
}

const jinjaTrim = text => text.replace(/^\s+|\s+$/g, '');

function renderContent(content) {
  if (typeof content === 'string') return content;
  if (content === null || content === undefined) return '';
  if (Array.isArray(content)) {
    return content.map(part => {
      if (part && typeof part === 'object' && typeof part.text === 'string') return part.text;
      throw new Error('qwen-chat-render covers text content only');
    }).join('');
  }
  throw new Error('Unexpected content type.');
}

function argumentsObject(value) {
  if (value && typeof value === 'object') return value;
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
    } catch { /* llama.cpp keeps an unparsable string; the template then renders no parameters */ }
  }
  return undefined;
}

function argumentText(value) {
  if (value && typeof value === 'object') return tojson(value);
  if (typeof value === 'string') return value;
  throw new Error('qwen-chat-render covers string and object arguments only');
}

/**
 * Render one chat request as llama-server does with the shipped Qwen3.5
 * template. `preserveThinking` is the chat_template_kwargs switch the shipped
 * template (and Qwen3.6's own template) honor; `enableThinking` is false for
 * ODS unless reasoning was enabled.
 */
export function renderQwen35({messages, tools = [], enableThinking = false, preserveThinking = false,
  addGenerationPrompt = true}) {
  if (!Array.isArray(messages) || messages.length === 0) throw new Error('No messages provided.');
  let out = '';
  const first = messages[0];
  if (tools.length) {
    out += '<|im_start|>system\n# Tools\n\nYou have access to the following functions:\n\n<tools>';
    // llama-server re-serializes each OpenAI tool as exactly these fields.
    for (const tool of tools) {
      const fn = tool.function ?? tool;
      out += `\n${tojson({type: 'function', function: {name: fn.name, description: fn.description ?? '',
        parameters: fn.parameters}})}`;
    }
    out += '\n</tools>' + TOOL_INSTRUCTIONS;
    if (first.role === 'system') {
      const content = jinjaTrim(renderContent(first.content));
      if (content) out += `\n\n${content}`;
    }
    out += '<|im_end|>\n';
  } else if (first.role === 'system') {
    out += `<|im_start|>system\n${jinjaTrim(renderContent(first.content))}<|im_end|>\n`;
  }
  let lastQuery = -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== 'user') continue;
    const content = jinjaTrim(renderContent(message.content));
    if (!(content.startsWith('<tool_response>') && content.endsWith('</tool_response>'))) { lastQuery = index; break; }
  }
  if (lastQuery === -1) throw new Error('No user query found in messages.');
  messages.forEach((message, index) => {
    let content = jinjaTrim(renderContent(message.content));
    if (message.role === 'system') {
      if (index !== 0) throw new Error('System message must be at the beginning.');
    } else if (message.role === 'user') {
      out += `<|im_start|>user\n${content}<|im_end|>\n`;
    } else if (message.role === 'assistant') {
      let reasoning = '';
      if (typeof message.reasoning_content === 'string' && message.reasoning_content) {
        reasoning = message.reasoning_content;
      } else if (content.includes('</think>')) {
        reasoning = content.split('</think>')[0].replace(/\n+$/, '').split('<think>').at(-1).replace(/^\n+/, '');
        content = content.split('</think>').at(-1).replace(/^\n+/, '');
      }
      reasoning = jinjaTrim(reasoning);
      if (preserveThinking || index > lastQuery) {
        out += `<|im_start|>assistant\n<think>\n${reasoning}\n</think>\n\n${content}`;
      } else {
        out += `<|im_start|>assistant\n${content}`;
      }
      (message.tool_calls ?? []).forEach((call, callIndex) => {
        const fn = call.function ?? call;
        if (callIndex === 0) out += jinjaTrim(content) ? `\n\n<tool_call>\n<function=${fn.name}>\n` : `<tool_call>\n<function=${fn.name}>\n`;
        else out += `\n<tool_call>\n<function=${fn.name}>\n`;
        const args = argumentsObject(fn.arguments);
        for (const [name, value] of Object.entries(args ?? {})) {
          out += `<parameter=${name}>\n${argumentText(value)}\n</parameter>\n`;
        }
        out += '</function>\n</tool_call>';
      });
      out += '<|im_end|>\n';
    } else if (message.role === 'tool') {
      if (index > 0 && messages[index - 1].role !== 'tool') out += '<|im_start|>user';
      out += `\n<tool_response>\n${content}\n</tool_response>`;
      if (index === messages.length - 1 || messages[index + 1].role !== 'tool') out += '<|im_end|>\n';
    } else {
      throw new Error('Unexpected message role.');
    }
  });
  if (addGenerationPrompt) out += `<|im_start|>assistant\n${enableThinking ? '<think>\n' : '<think>\n\n</think>\n\n'}`;
  return out;
}

export function commonPrefixLength(left, right) {
  let index = 0;
  const limit = Math.min(left.length, right.length);
  while (index < limit && left.charCodeAt(index) === right.charCodeAt(index)) index += 1;
  return index;
}
