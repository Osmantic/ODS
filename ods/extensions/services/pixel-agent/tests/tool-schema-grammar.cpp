// Model-free bridge to the exact llama.cpp schema converter and grammar engine.
// Build instructions and the pinned runtime live in test-pixel-tool-grammar.yml.
#include "json-schema-to-grammar.h"
#include "chat.h"
#include "llama-grammar.h"
#include "unicode.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <memory>

int main() try {
    const auto input = nlohmann::ordered_json::parse(std::cin);
    std::string grammar_text;
    if (input.contains("chatTemplate")) {
        // Run the same template/tool grammar composition as llama-server. No
        // model, tokens, inference server or owner content is needed.
        const auto templates = common_chat_templates_init(nullptr, input.at("chatTemplate").get<std::string>());
        common_chat_templates_inputs request;
        common_chat_msg message;
        message.role = "user";
        message.content = "Use a tool.";
        request.messages.push_back(message);
        request.tool_choice = COMMON_CHAT_TOOL_CHOICE_REQUIRED;
        request.enable_thinking = false;
        for (const auto & tool : input.at("tools")) {
            request.tools.push_back({tool.at("name"), tool.value("description", ""), tool.at("parameters").dump()});
        }
        grammar_text = common_chat_templates_apply(templates.get(), request).grammar;
        if (grammar_text.empty()) throw std::runtime_error("Chat template did not produce a tool grammar");
        for (const auto & tool : request.tools) {
            if (grammar_text.find(tool.name) == std::string::npos) {
                throw std::runtime_error("Chat grammar omitted offered tool: " + tool.name);
            }
        }
    } else {
        grammar_text = json_schema_to_grammar(input.at("schema"), true);
    }
    std::unique_ptr<llama_grammar, decltype(&llama_grammar_free_impl)> grammar(
        llama_grammar_init_impl(nullptr, grammar_text.c_str(), "root", false, nullptr, 0, nullptr, 0),
        llama_grammar_free_impl);
    if (!grammar) return 1;
    if (input.value("compileOnly", false)) {
        std::cout << nlohmann::ordered_json{{"grammarBytes", grammar_text.size()}}.dump();
        return 0;
    }
    for (const auto codepoint : unicode_cpts_from_utf8(input.at("arguments").dump())) {
        llama_grammar_accept(grammar.get(), codepoint);
        if (llama_grammar_get_stacks(grammar.get()).empty()) return 2;
    }
    for (const auto & stack : llama_grammar_get_stacks(grammar.get())) {
        if (stack.empty()) return 0;
    }
    return 2;
} catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 3;
}
