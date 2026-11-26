#include "json.h"
#include <cstdio>

static int failures = 0;
#define CHECK(x) do { if (!(x)) { std::printf("FAIL line %d: %s\n", __LINE__, #x); ++failures; } } while (0)

int main() {
    json::Value v;
    CHECK(json::parse(R"({"a":[1,2.5,true,null,"xé😀\n"],"b":{"c":"d"},"visible":false})", v));
    CHECK(v["a"].items().size() == 5);
    CHECK(v["a"].items()[1].asNumber() == 2.5);
    CHECK(v["a"].items()[2].asBool());
    CHECK(v["a"].items()[4].asString() == "x\xC3\xA9\xF0\x9F\x98\x80\n");
    CHECK(v["b"]["c"].asString() == "d");
    CHECK(v["missing"]["deeper"].asString().empty());
    CHECK(!v["visible"].asBool(true));
    json::Value round;
    CHECK(json::parse(v.dump(), round));
    CHECK(round == v);
    json::Value bad;
    CHECK(!json::parse("{\"a\":", bad));
    CHECK(!json::parse("[1,2", bad));
    CHECK(!json::parse("{} trailing", bad));
    CHECK(!json::parse(std::string(200, '['), bad));
    json::Value msg = json::Value::object();
    msg.set("command", "x");
    msg.set("keys", json::Value::array());
    CHECK(msg.dump() == "{\"command\":\"x\",\"keys\":[]}");
    std::printf(failures ? "FAILED\n" : "ok\n");
    return failures;
}
