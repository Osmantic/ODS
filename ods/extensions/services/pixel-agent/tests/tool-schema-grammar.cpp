// Model-free bridge to the exact llama.cpp schema converter and grammar engine.
// Build instructions and the pinned runtime live in test-pixel-tool-grammar.yml.
#include "json-schema-to-grammar.h"
#include "llama-grammar.h"
#include "unicode.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <memory>

int main() {
    const auto input = nlohmann::ordered_json::parse(std::cin);
    const auto grammar_text = json_schema_to_grammar(input.at("schema"), true);
    std::unique_ptr<llama_grammar, decltype(&llama_grammar_free_impl)> grammar(
        llama_grammar_init_impl(nullptr, grammar_text.c_str(), "root", false, nullptr, 0, nullptr, 0),
        llama_grammar_free_impl);
    if (!grammar) return 1;
    for (const auto codepoint : unicode_cpts_from_utf8(input.at("arguments").dump())) {
        llama_grammar_accept(grammar.get(), codepoint);
        if (llama_grammar_get_stacks(grammar.get()).empty()) return 2;
    }
    for (const auto & stack : llama_grammar_get_stacks(grammar.get())) {
        if (stack.empty()) return 0;
    }
    return 2;
}
