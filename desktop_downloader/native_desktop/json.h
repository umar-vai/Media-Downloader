// Minimal JSON value, parser and writer (UTF-8). Enough for the line protocol with the Python controller.
#pragma once

#include <cstdint>
#include <cstdlib>
#include <map>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace json {

class Value {
public:
    enum class Type { Null, Bool, Number, String, Array, Object };

    Value() = default;
    Value(bool b) : type_(Type::Bool), bool_(b) {}
    Value(int n) : type_(Type::Number), number_(n) {}
    Value(double n) : type_(Type::Number), number_(n) {}
    Value(const char *s) : type_(Type::String), string_(s) {}
    Value(std::string s) : type_(Type::String), string_(std::move(s)) {}

    static Value array() { Value v; v.type_ = Type::Array; return v; }
    static Value object() { Value v; v.type_ = Type::Object; return v; }

    Type type() const { return type_; }
    bool isObject() const { return type_ == Type::Object; }
    bool isArray() const { return type_ == Type::Array; }
    bool isString() const { return type_ == Type::String; }

    bool asBool(bool fallback = false) const { return type_ == Type::Bool ? bool_ : fallback; }
    int asInt(int fallback = 0) const { return type_ == Type::Number ? static_cast<int>(number_) : fallback; }
    double asNumber(double fallback = 0) const { return type_ == Type::Number ? number_ : fallback; }
    const std::string &asString() const { return type_ == Type::String ? string_ : empty(); }

    const std::vector<Value> &items() const { return type_ == Type::Array ? array_ : emptyArray(); }
    void push(Value v) { if (type_ == Type::Array) array_.push_back(std::move(v)); }

    // Missing keys and wrong types yield a shared null value, so chained lookups never fail.
    const Value &operator[](const std::string &key) const {
        if (type_ == Type::Object) {
            auto it = object_.find(key);
            if (it != object_.end()) return it->second;
        }
        return null();
    }
    void set(const std::string &key, Value v) { if (type_ == Type::Object) object_[key] = std::move(v); }
    bool has(const std::string &key) const { return type_ == Type::Object && object_.count(key) != 0; }

    bool operator==(const Value &o) const {
        if (type_ != o.type_) return false;
        switch (type_) {
        case Type::Null: return true;
        case Type::Bool: return bool_ == o.bool_;
        case Type::Number: return number_ == o.number_;
        case Type::String: return string_ == o.string_;
        case Type::Array: return array_ == o.array_;
        case Type::Object: return object_ == o.object_;
        }
        return false;
    }
    bool operator!=(const Value &o) const { return !(*this == o); }

    std::string dump() const {
        std::string out;
        write(out);
        return out;
    }

private:
    static const std::string &empty() { static const std::string s; return s; }
    static const std::vector<Value> &emptyArray() { static const std::vector<Value> v; return v; }
    static const Value &null() { static const Value v; return v; }

    static void quote(std::string &out, const std::string &s) {
        static const char *hex = "0123456789abcdef";
        out += '"';
        for (unsigned char c : s) {
            switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    out += "\\u00";
                    out += hex[c >> 4];
                    out += hex[c & 15];
                } else {
                    out += static_cast<char>(c);
                }
            }
        }
        out += '"';
    }

    void write(std::string &out) const {
        switch (type_) {
        case Type::Null: out += "null"; break;
        case Type::Bool: out += bool_ ? "true" : "false"; break;
        case Type::Number: {
            if (number_ == static_cast<long long>(number_)) out += std::to_string(static_cast<long long>(number_));
            else out += std::to_string(number_);
            break;
        }
        case Type::String: quote(out, string_); break;
        case Type::Array: {
            out += '[';
            bool first = true;
            for (const auto &v : array_) { if (!first) out += ','; first = false; v.write(out); }
            out += ']';
            break;
        }
        case Type::Object: {
            out += '{';
            bool first = true;
            for (const auto &kv : object_) {
                if (!first) out += ',';
                first = false;
                quote(out, kv.first);
                out += ':';
                kv.second.write(out);
            }
            out += '}';
            break;
        }
        }
    }

    Type type_ = Type::Null;
    bool bool_ = false;
    double number_ = 0;
    std::string string_;
    std::vector<Value> array_;
    std::map<std::string, Value> object_;
};

class Parser {
public:
    explicit Parser(const std::string &text) : s_(text) {}

    // Returns false on malformed input (the caller drops the line).
    bool parse(Value &out) {
        skip();
        if (!value(out, 0)) return false;
        skip();
        return pos_ == s_.size();
    }

private:
    void skip() { while (pos_ < s_.size() && (s_[pos_] == ' ' || s_[pos_] == '\t' || s_[pos_] == '\r' || s_[pos_] == '\n')) ++pos_; }
    bool literal(const char *word) {
        size_t n = 0;
        while (word[n]) ++n;
        if (s_.compare(pos_, n, word) != 0) return false;
        pos_ += n;
        return true;
    }

    static void utf8(std::string &out, unsigned cp) {
        if (cp < 0x80) out += static_cast<char>(cp);
        else if (cp < 0x800) { out += static_cast<char>(0xC0 | (cp >> 6)); out += static_cast<char>(0x80 | (cp & 0x3F)); }
        else if (cp < 0x10000) {
            out += static_cast<char>(0xE0 | (cp >> 12));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (cp >> 18));
            out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        }
    }

    bool hex4(unsigned &cp) {
        if (pos_ + 4 > s_.size()) return false;
        cp = 0;
        for (int i = 0; i < 4; ++i) {
            char c = s_[pos_++];
            cp <<= 4;
            if (c >= '0' && c <= '9') cp |= c - '0';
            else if (c >= 'a' && c <= 'f') cp |= c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') cp |= c - 'A' + 10;
            else return false;
        }
        return true;
    }

    bool string(std::string &out) {
        if (pos_ >= s_.size() || s_[pos_] != '"') return false;
        ++pos_;
        while (pos_ < s_.size()) {
            char c = s_[pos_++];
            if (c == '"') return true;
            if (c != '\\') { out += c; continue; }
            if (pos_ >= s_.size()) return false;
            char e = s_[pos_++];
            switch (e) {
            case '"': out += '"'; break;
            case '\\': out += '\\'; break;
            case '/': out += '/'; break;
            case 'b': out += '\b'; break;
            case 'f': out += '\f'; break;
            case 'n': out += '\n'; break;
            case 'r': out += '\r'; break;
            case 't': out += '\t'; break;
            case 'u': {
                unsigned cp;
                if (!hex4(cp)) return false;
                if (cp >= 0xD800 && cp < 0xDC00) {
                    unsigned low;
                    if (s_.compare(pos_, 2, "\\u") != 0) return false;
                    pos_ += 2;
                    if (!hex4(low) || low < 0xDC00 || low > 0xDFFF) return false;
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                }
                utf8(out, cp);
                break;
            }
            default: return false;
            }
        }
        return false;
    }

    bool value(Value &out, int depth) {
        if (depth > 64 || pos_ >= s_.size()) return false;
        char c = s_[pos_];
        if (c == '{') {
            ++pos_;
            out = Value::object();
            skip();
            if (pos_ < s_.size() && s_[pos_] == '}') { ++pos_; return true; }
            for (;;) {
                skip();
                std::string key;
                if (!string(key)) return false;
                skip();
                if (pos_ >= s_.size() || s_[pos_++] != ':') return false;
                skip();
                Value v;
                if (!value(v, depth + 1)) return false;
                out.set(key, std::move(v));
                skip();
                if (pos_ >= s_.size()) return false;
                if (s_[pos_] == ',') { ++pos_; continue; }
                if (s_[pos_] == '}') { ++pos_; return true; }
                return false;
            }
        }
        if (c == '[') {
            ++pos_;
            out = Value::array();
            skip();
            if (pos_ < s_.size() && s_[pos_] == ']') { ++pos_; return true; }
            for (;;) {
                skip();
                Value v;
                if (!value(v, depth + 1)) return false;
                out.push(std::move(v));
                skip();
                if (pos_ >= s_.size()) return false;
                if (s_[pos_] == ',') { ++pos_; continue; }
                if (s_[pos_] == ']') { ++pos_; return true; }
                return false;
            }
        }
        if (c == '"') {
            std::string text;
            if (!string(text)) return false;
            out = Value(std::move(text));
            return true;
        }
        if (c == 't') { out = Value(true); return literal("true"); }
        if (c == 'f') { out = Value(false); return literal("false"); }
        if (c == 'n') { out = Value(); return literal("null"); }
        char *end = nullptr;
        double number = std::strtod(s_.c_str() + pos_, &end);
        if (end == s_.c_str() + pos_) return false;
        pos_ = static_cast<size_t>(end - s_.c_str());
        out = Value(number);
        return true;
    }

    const std::string &s_;
    size_t pos_ = 0;
};

inline bool parse(const std::string &text, Value &out) { return Parser(text).parse(out); }

} // namespace json
