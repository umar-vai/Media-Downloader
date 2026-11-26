// The app's own icon set (a C++ port of icons.py): every glyph is a few simple shapes drawn at
// 4x with GDI+ and scaled down, so icons stay crisp at any display scaling.
#pragma once

#include "common.h"

#include <objidl.h>
#include <gdiplus.h>

namespace md {
namespace icons {

namespace G = Gdiplus;

inline ULONG_PTR g_gdiplusToken = 0;

inline void startup() {
    if (g_gdiplusToken) return;
    G::GdiplusStartupInput input;
    G::GdiplusStartup(&g_gdiplusToken, &input, nullptr);
}

inline void shutdown() {
    if (g_gdiplusToken) G::GdiplusShutdown(g_gdiplusToken);
    g_gdiplusToken = 0;
}

inline G::Color col(unsigned rgb, BYTE a = 255) { return G::Color(a, (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF); }

constexpr unsigned BLUE = 0x2563EB, GREEN = 0x16A34A, ORANGE = 0xEA580C, AMBER = 0xCA8A04, RED = 0xDC2626,
                   VIOLET = 0x7C3AED, TEAL = 0x0D9488, SLATE = 0x64748B, WHITE = 0xFFFFFF;
constexpr unsigned TOOL_GREEN = 0x2E8B3E, TOOL_RED = 0xC42B1C, TOOL_BLUE = 0x2F6FBA;
constexpr unsigned FOLDER_BACK = 0xDDA82E, FOLDER_FRONT = 0xF4CF6B;

// Drawing in "unit" coordinates (0..1 of the icon), like the Pillow painters.
class Painter {
public:
    Painter(G::Graphics &g, float u) : g_(g), u_(u) {}

    void rrect(float x0, float y0, float x1, float y1, float r, const G::Color *fillColor,
               const G::Color *outline = nullptr, float width = 0) {
        G::GraphicsPath path;
        float X0 = x0 * u_, Y0 = y0 * u_, X1 = x1 * u_, Y1 = y1 * u_;
        float R = std::min(r * u_, std::min(X1 - X0, Y1 - Y0) / 2.0f);
        if (R <= 0.5f) {
            path.AddRectangle(G::RectF(X0, Y0, X1 - X0, Y1 - Y0));
        } else {
            float d = R * 2;
            path.AddArc(X0, Y0, d, d, 180, 90);
            path.AddArc(X1 - d, Y0, d, d, 270, 90);
            path.AddArc(X1 - d, Y1 - d, d, d, 0, 90);
            path.AddArc(X0, Y1 - d, d, d, 90, 90);
            path.CloseFigure();
        }
        if (fillColor) {
            G::SolidBrush b(*fillColor);
            g_.FillPath(&b, &path);
        }
        if (outline) {
            G::Pen p(*outline, std::max(1.0f, std::floor(width * u_)));
            p.SetAlignment(G::PenAlignmentInset);
            g_.DrawPath(&p, &path);
        }
    }
    void rrect(float x0, float y0, float x1, float y1, float r, unsigned fillRgb) {
        G::Color c = col(fillRgb);
        rrect(x0, y0, x1, y1, r, &c);
    }
    void rrectOutline(float x0, float y0, float x1, float y1, float r, unsigned fillRgb, bool hasFill, unsigned edge,
                      float width) {
        G::Color f = col(fillRgb), e = col(edge);
        rrect(x0, y0, x1, y1, r, hasFill ? &f : nullptr, &e, width);
    }

    void circle(float cx, float cy, float r, const G::Color *fillColor, const G::Color *outline = nullptr, float width = 0) {
        G::RectF box((cx - r) * u_, (cy - r) * u_, 2 * r * u_, 2 * r * u_);
        if (fillColor) {
            G::SolidBrush b(*fillColor);
            g_.FillEllipse(&b, box);
        }
        if (outline) {
            G::Pen p(*outline, std::max(1.0f, std::floor(width * u_)));
            p.SetAlignment(G::PenAlignmentInset);
            g_.DrawEllipse(&p, box);
        }
    }
    void circle(float cx, float cy, float r, unsigned rgb) {
        G::Color c = col(rgb);
        circle(cx, cy, r, &c);
    }

    void poly(std::initializer_list<std::pair<float, float>> pts, unsigned rgb) {
        std::vector<G::PointF> v;
        for (auto &p : pts) v.emplace_back(p.first * u_, p.second * u_);
        G::SolidBrush b(col(rgb));
        g_.FillPolygon(&b, v.data(), static_cast<INT>(v.size()));
    }
    void poly(const std::vector<std::pair<float, float>> &pts, unsigned rgb) {
        std::vector<G::PointF> v;
        for (auto &p : pts) v.emplace_back(p.first * u_, p.second * u_);
        G::SolidBrush b(col(rgb));
        g_.FillPolygon(&b, v.data(), static_cast<INT>(v.size()));
    }

    void line(std::initializer_list<std::pair<float, float>> pts, unsigned rgb, float width) {
        std::vector<G::PointF> v;
        for (auto &p : pts) v.emplace_back(p.first * u_, p.second * u_);
        G::Pen p(col(rgb), std::max(1.0f, std::floor(width * u_)));
        p.SetLineJoin(G::LineJoinRound);
        g_.DrawLines(&p, v.data(), static_cast<INT>(v.size()));
    }

    void arc(float x0, float y0, float x1, float y1, float start, float sweep, unsigned rgb, float width) {
        G::Pen p(col(rgb), std::max(1.0f, std::floor(width * u_)));
        g_.DrawArc(&p, x0 * u_, y0 * u_, (x1 - x0) * u_, (y1 - y0) * u_, start, sweep);
    }

    void ellipseOutline(float x0, float y0, float x1, float y1, unsigned rgb, float width) {
        G::Pen p(col(rgb), std::max(1.0f, std::floor(width * u_)));
        p.SetAlignment(G::PenAlignmentInset);
        g_.DrawEllipse(&p, x0 * u_, y0 * u_, (x1 - x0) * u_, (y1 - y0) * u_);
    }

    // Punches a transparent hole (Pillow's fill="#00000000").
    void clearCircle(float cx, float cy, float r) {
        G::CompositingMode old = g_.GetCompositingMode();
        g_.SetCompositingMode(G::CompositingModeSourceCopy);
        G::SolidBrush b(G::Color(0, 0, 0, 0));
        g_.FillEllipse(&b, (cx - r) * u_, (cy - r) * u_, 2 * r * u_, 2 * r * u_);
        g_.SetCompositingMode(old);
    }

    // ------------------------------------------------------------------ glyph parts
    void play(float cx, float cy, float s, unsigned c) {
        poly({{cx - s * 0.42f, cy - s * 0.55f}, {cx - s * 0.42f, cy + s * 0.55f}, {cx + s * 0.58f, cy}}, c);
    }
    void pause(float cx, float cy, float s, unsigned c) {
        float w = s * 0.2f, h = s * 0.5f;
        rrect(cx - s * 0.3f, cy - h, cx - s * 0.3f + w, cy + h, w * 0.3f, c);
        rrect(cx + s * 0.1f, cy - h, cx + s * 0.1f + w, cy + h, w * 0.3f, c);
    }
    void check(float cx, float cy, float s, unsigned c, float width) {
        line({{cx - s * 0.45f, cy}, {cx - s * 0.12f, cy + s * 0.32f}, {cx + s * 0.48f, cy - s * 0.34f}}, c, width);
    }
    void cross(float cx, float cy, float s, unsigned c, float width) {
        line({{cx - s * 0.36f, cy - s * 0.36f}, {cx + s * 0.36f, cy + s * 0.36f}}, c, width);
        line({{cx - s * 0.36f, cy + s * 0.36f}, {cx + s * 0.36f, cy - s * 0.36f}}, c, width);
    }
    void arrowDown(float cx, float cy, float s, unsigned c, float width) {
        line({{cx, cy - s * 0.48f}, {cx, cy + s * 0.3f}}, c, width);
        line({{cx - s * 0.36f, cy - s * 0.02f}, {cx, cy + s * 0.4f}, {cx + s * 0.36f, cy - s * 0.02f}}, c, width);
    }
    void lines(float x, float y, float w, float gap, int count, unsigned c, float width) {
        for (int i = 0; i < count; ++i) line({{x, y + i * gap}, {x + w, y + i * gap}}, c, width);
    }
    void badge(float cx, float cy, float r, unsigned fillRgb, unsigned ring) {
        circle(cx, cy, r + 0.06f, ring);
        circle(cx, cy, r, fillRgb);
    }
    void page(float x0, float y0, float x1, float y1, unsigned ink, unsigned fillRgb = WHITE) {
        float fold = (x1 - x0) * 0.32f;
        std::vector<G::PointF> v{{x0 * u_, y0 * u_}, {(x1 - fold) * u_, y0 * u_}, {x1 * u_, (y0 + fold) * u_},
                                 {x1 * u_, y1 * u_}, {x0 * u_, y1 * u_}};
        G::SolidBrush b(col(fillRgb));
        g_.FillPolygon(&b, v.data(), static_cast<INT>(v.size()));
        G::Pen p(col(ink), std::max(1.0f, std::floor(0.07f * u_)));
        g_.DrawPolygon(&p, v.data(), static_cast<INT>(v.size()));
        line({{x1 - fold, y0}, {x1 - fold, y0 + fold}, {x1, y0 + fold}}, ink, 0.06f);
    }
    void plainFolder() {
        rrect(0.06f, 0.16f, 0.46f, 0.32f, 0.04f, FOLDER_BACK);
        rrect(0.06f, 0.24f, 0.94f, 0.86f, 0.05f, FOLDER_BACK);
        rrect(0.06f, 0.36f, 0.94f, 0.86f, 0.05f, FOLDER_FRONT);
    }

private:
    G::Graphics &g_;
    float u_;
};

inline unsigned rgbOf(COLORREF c) { return (GetRValue(c) << 16) | (GetGValue(c) << 8) | GetBValue(c); }

inline void paintTool(Painter &d, const std::string &name, unsigned ink) {
    if (name == "add") {
        d.page(0.14f, 0.06f, 0.74f, 0.94f, ink);
        d.line({{0.72f, 0.42f}, {0.72f, 0.94f}}, TOOL_GREEN, 0.15f);
        d.line({{0.46f, 0.68f}, {0.98f, 0.68f}}, TOOL_GREEN, 0.15f);
    } else if (name == "batch") {
        d.page(0.30f, 0.04f, 0.92f, 0.74f, ink);
        d.page(0.10f, 0.24f, 0.72f, 0.96f, ink);
        d.lines(0.22f, 0.52f, 0.36f, 0.14f, 3, ink, 0.06f);
    } else if (name == "resume") {
        d.poly({{0.22f, 0.10f}, {0.22f, 0.90f}, {0.86f, 0.50f}}, TOOL_GREEN);
    } else if (name == "stop") {
        d.rrect(0.18f, 0.18f, 0.82f, 0.82f, 0.05f, TOOL_RED);
    } else if (name == "queue_start") {
        d.poly({{0.06f, 0.16f}, {0.06f, 0.84f}, {0.52f, 0.50f}}, TOOL_GREEN);
        d.poly({{0.48f, 0.16f}, {0.48f, 0.84f}, {0.94f, 0.50f}}, TOOL_GREEN);
    } else if (name == "stop_all") {
        d.rrect(0.34f, 0.08f, 0.92f, 0.66f, 0.04f, 0xE08A80);
        d.rrect(0.08f, 0.34f, 0.66f, 0.92f, 0.04f, TOOL_RED);
    } else if (name == "delete") {
        d.line({{0.18f, 0.18f}, {0.82f, 0.82f}}, TOOL_RED, 0.17f);
        d.line({{0.82f, 0.18f}, {0.18f, 0.82f}}, TOOL_RED, 0.17f);
    } else if (name == "delete_done") {
        d.line({{0.08f, 0.08f}, {0.62f, 0.62f}}, TOOL_RED, 0.15f);
        d.line({{0.62f, 0.08f}, {0.08f, 0.62f}}, TOOL_RED, 0.15f);
        d.check(0.70f, 0.74f, 0.46f, TOOL_GREEN, 0.13f);
    } else if (name == "cleanup") {
        for (float y : {0.20f, 0.42f, 0.64f}) d.line({{0.08f, y}, {y > 0.5f ? 0.56f : 0.92f, y}}, ink, 0.09f);
        d.line({{0.58f, 0.58f}, {0.94f, 0.94f}}, TOOL_RED, 0.14f);
        d.line({{0.94f, 0.58f}, {0.58f, 0.94f}}, TOOL_RED, 0.14f);
    } else if (name == "settings") {
        const float cx = 0.5f, cy = 0.5f;
        for (int i = 0; i < 8; ++i) {
            float a = static_cast<float>(i * 3.14159265358979 / 4), ca = std::cos(a), sa = std::sin(a);
            std::vector<std::pair<float, float>> pts;
            for (auto [dx, dy] : {std::pair<float, float>{-0.09f, 0.28f}, {0.09f, 0.28f}, {0.08f, 0.46f}, {-0.08f, 0.46f}})
                pts.emplace_back(cx + dx * ca - dy * sa, cy + dx * sa + dy * ca);
            d.poly(pts, ink);
        }
        d.circle(cx, cy, 0.33f, ink);
        d.clearCircle(cx, cy, 0.13f);
    } else if (name == "browser") {
        G::Color b = col(TOOL_BLUE);
        d.circle(0.5f, 0.5f, 0.43f, nullptr, &b, 0.08f);
        d.ellipseOutline(0.31f, 0.07f, 0.69f, 0.93f, TOOL_BLUE, 0.07f);
        d.line({{0.07f, 0.5f}, {0.93f, 0.5f}}, TOOL_BLUE, 0.07f);
    } else if (name == "editor") {
        d.rrect(0.06f, 0.18f, 0.94f, 0.82f, 0.06f, ink);
        for (float fx : {0.18f, 0.39f, 0.61f, 0.82f}) {
            d.rrect(fx - 0.06f, 0.24f, fx + 0.06f, 0.32f, 0.01f, WHITE);
            d.rrect(fx - 0.06f, 0.68f, fx + 0.06f, 0.76f, 0.01f, WHITE);
        }
        d.rrect(0.12f, 0.38f, 0.88f, 0.62f, 0.02f, 0xD7E6F5);
    } else if (name == "record") {
        d.rrect(0.04f, 0.10f, 0.96f, 0.74f, 0.06f, ink);
        d.rrect(0.11f, 0.17f, 0.89f, 0.67f, 0.02f, WHITE);
        d.rrect(0.40f, 0.74f, 0.60f, 0.86f, 0.0f, ink);
        d.rrect(0.26f, 0.86f, 0.74f, 0.94f, 0.03f, ink);
        d.circle(0.5f, 0.42f, 0.17f, TOOL_RED);
    } else if (name == "mic") {
        d.rrect(0.36f, 0.06f, 0.64f, 0.56f, 0.14f, TOOL_RED);
        d.arc(0.22f, 0.28f, 0.78f, 0.78f, 0, 180, ink, 0.07f);
        d.line({{0.5f, 0.78f}, {0.5f, 0.92f}}, ink, 0.07f);
        d.line({{0.34f, 0.93f}, {0.66f, 0.93f}}, ink, 0.07f);
    } else if (name == "update") {
        d.line({{0.5f, 0.08f}, {0.5f, 0.70f}}, TOOL_GREEN, 0.15f);
        d.line({{0.20f, 0.44f}, {0.5f, 0.76f}, {0.80f, 0.44f}}, TOOL_GREEN, 0.15f);
        d.line({{0.12f, 0.92f}, {0.88f, 0.92f}}, ink, 0.08f);
    } else if (name == "folder") {
        d.rrect(0.06f, 0.16f, 0.48f, 0.34f, 0.06f, BLUE);
        d.rrect(0.06f, 0.26f, 0.94f, 0.86f, 0.08f, BLUE);
        d.rrect(0.06f, 0.36f, 0.94f, 0.86f, 0.08f, 0x5384EE);
    } else if (name == "scissors") {
        d.circle(0.28f, 0.74f, 0.14f, nullptr, nullptr);
        G::Color c = col(ink);
        d.circle(0.28f, 0.76f, 0.14f, nullptr, &c, 0.07f);
        d.circle(0.72f, 0.76f, 0.14f, nullptr, &c, 0.07f);
        d.line({{0.36f, 0.64f}, {0.78f, 0.08f}}, ink, 0.07f);
        d.line({{0.64f, 0.64f}, {0.22f, 0.08f}}, ink, 0.07f);
    } else {
        d.circle(0.5f, 0.5f, 0.4f, SLATE);
    }
}

inline unsigned categoryColor(const std::string &category) {
    if (category == "video") return VIOLET;
    if (category == "audio") return ORANGE;
    if (category == "compressed") return AMBER;
    if (category == "documents") return BLUE;
    if (category == "programs") return TEAL;
    return SLATE;
}

inline bool stateBadge(const std::string &state, unsigned &out) {
    static const std::map<std::string, unsigned> badges{
        {"complete", GREEN}, {"downloading", BLUE}, {"connecting", BLUE}, {"finishing", BLUE}, {"failed", RED},
        {"paused", ORANGE}, {"stopping", ORANGE},     {"queued", SLATE},   {"retrying", AMBER}, {"cancelled", SLATE},
        {"missing", AMBER}};
    auto it = badges.find(state);
    if (it == badges.end()) return false;
    out = it->second;
    return true;
}

inline void paintFile(Painter &d, const std::string &category, const std::string &state, unsigned ring) {
    unsigned color = categoryColor(category);
    d.rrect(0.04f, 0.04f, 0.86f, 0.86f, 0.2f, color);
    float cx = 0.45f, cy = 0.45f;
    if (category == "video") {
        d.play(cx + 0.02f, cy, 0.42f, WHITE);
    } else if (category == "audio") {
        d.circle(0.36f, 0.6f, 0.11f, WHITE);
        d.line({{0.45f, 0.6f}, {0.45f, 0.2f}}, WHITE, 0.07f);
        d.poly({{0.45f, 0.18f}, {0.66f, 0.26f}, {0.66f, 0.36f}, {0.45f, 0.30f}}, WHITE);
    } else if (category == "compressed") {
        int i = 0;
        for (float y : {0.14f, 0.26f, 0.38f}) {
            float x = (i++ % 2) ? 0.41f : 0.47f;
            d.rrect(x, y, x + 0.06f, y + 0.08f, 0.01f, WHITE);
        }
        d.rrect(0.36f, 0.5f, 0.54f, 0.72f, 0.04f, WHITE);
    } else if (category == "documents") {
        d.lines(0.22f, 0.27f, 0.46f, 0.14f, 4, WHITE, 0.07f);
    } else if (category == "programs") {
        G::Color w = col(WHITE);
        d.rrect(0.18f, 0.2f, 0.72f, 0.7f, 0.06f, nullptr, &w, 0.07f);
        d.line({{0.18f, 0.33f}, {0.72f, 0.33f}}, WHITE, 0.07f);
    } else {
        for (float x : {0.27f, 0.45f, 0.63f}) d.circle(x, 0.45f, 0.06f, WHITE);
    }
    unsigned badge = 0;
    if (!stateBadge(state, badge)) return;
    const float bx = 0.76f, by = 0.76f;
    d.badge(bx, by, 0.2f, badge, ring);
    if (state == "complete") d.check(bx, by, 0.22f, WHITE, 0.06f);
    else if (state == "downloading" || state == "connecting" || state == "finishing") d.arrowDown(bx, by, 0.24f, WHITE, 0.06f);
    else if (state == "failed" || state == "cancelled") d.cross(bx, by, 0.2f, WHITE, 0.06f);
    else if (state == "paused" || state == "stopping") d.pause(bx, by, 0.22f, WHITE);
    else if (state == "missing") {
        d.line({{bx, by - 0.1f}, {bx, by + 0.02f}}, WHITE, 0.06f);
        d.circle(bx, by + 0.09f, 0.03f, WHITE);
    } else {
        d.line({{bx, by - 0.1f}, {bx, by}, {bx + 0.08f, by + 0.04f}}, WHITE, 0.05f);
    }
}

inline void paintTree(Painter &d, const std::string &name, unsigned ink) {
    const unsigned lineInk = 0x5A5A5A, blue = 0x2F6FBA;
    if (name == "queue") {
        d.lines(0.12f, 0.25f, 0.76f, 0.25f, 3, ink, 0.1f);
    } else if (name == "video") {
        d.rrect(0.04f, 0.16f, 0.96f, 0.84f, 0.06f, 0x3C3C3C);
        for (float x : {0.1f, 0.3f, 0.5f, 0.7f}) {
            d.rrect(x, 0.2f, x + 0.12f, 0.28f, 0.01f, WHITE);
            d.rrect(x, 0.72f, x + 0.12f, 0.8f, 0.01f, WHITE);
        }
        d.rrect(0.1f, 0.34f, 0.9f, 0.66f, 0.02f, 0x5B9BD5);
        d.play(0.52f, 0.5f, 0.26f, WHITE);
    } else if (name == "audio") {
        d.circle(0.26f, 0.78f, 0.15f, blue);
        d.circle(0.74f, 0.68f, 0.15f, blue);
        d.line({{0.38f, 0.78f}, {0.38f, 0.18f}}, blue, 0.09f);
        d.line({{0.86f, 0.68f}, {0.86f, 0.08f}}, blue, 0.09f);
        d.poly({{0.34f, 0.14f}, {0.9f, 0.04f}, {0.9f, 0.2f}, {0.34f, 0.3f}}, blue);
    } else if (name == "compressed") {
        d.rrectOutline(0.12f, 0.08f, 0.88f, 0.92f, 0.06f, FOLDER_FRONT, true, FOLDER_BACK, 0.06f);
        int i = 0;
        for (float y : {0.12f, 0.22f, 0.32f, 0.42f}) {
            float x = (i++ % 2) ? 0.44f : 0.5f;
            d.rrect(x, y, x + 0.08f, y + 0.08f, 0.01f, lineInk);
        }
        d.rrect(0.4f, 0.54f, 0.6f, 0.74f, 0.03f, lineInk);
    } else if (name == "documents") {
        d.page(0.16f, 0.04f, 0.84f, 0.96f, lineInk);
        d.lines(0.28f, 0.46f, 0.44f, 0.14f, 3, blue, 0.07f);
    } else if (name == "programs") {
        d.rrectOutline(0.06f, 0.14f, 0.94f, 0.86f, 0.04f, WHITE, true, lineInk, 0.06f);
        d.rrect(0.06f, 0.14f, 0.94f, 0.34f, 0.04f, blue);
        d.rrect(0.72f, 0.19f, 0.86f, 0.29f, 0.01f, WHITE);
    } else if (name == "misc") {
        d.page(0.16f, 0.04f, 0.84f, 0.96f, lineInk);
    } else if (name == "unfinished") {
        d.plainFolder();
        d.arrowDown(0.62f, 0.62f, 0.42f, blue, 0.11f);
    } else if (name == "finished") {
        d.plainFolder();
        d.check(0.6f, 0.62f, 0.46f, 0x2E8B3E, 0.11f);
    } else {
        d.plainFolder();
    }
}

// Renders a painter at 4x and scales it down into a premultiplied 32-bit DIB.
inline HBITMAP render(int size, const std::function<void(Painter &)> &paint, bool disabled = false) {
    startup();
    const int ss = 4, big = size * ss;
    G::Bitmap large(big, big, PixelFormat32bppPARGB);
    {
        G::Graphics g(&large);
        g.SetSmoothingMode(G::SmoothingModeAntiAlias);
        g.SetPixelOffsetMode(G::PixelOffsetModeHalf);
        g.Clear(G::Color(0, 0, 0, 0));
        Painter p(g, static_cast<float>(big));
        paint(p);
    }
    BITMAPINFO bi{};
    bi.bmiHeader.biSize = sizeof bi.bmiHeader;
    bi.bmiHeader.biWidth = size;
    bi.bmiHeader.biHeight = -size;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    void *bits = nullptr;
    HBITMAP dib = CreateDIBSection(nullptr, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (!dib) return nullptr;
    G::Bitmap target(size, size, size * 4, PixelFormat32bppPARGB, static_cast<BYTE *>(bits));
    {
        G::Graphics g(&target);
        g.Clear(G::Color(0, 0, 0, 0));
        g.SetInterpolationMode(G::InterpolationModeHighQualityBicubic);
        g.SetPixelOffsetMode(G::PixelOffsetModeHalf);
        g.DrawImage(&large, G::Rect(0, 0, size, size), 0, 0, big, big, G::UnitPixel);
    }
    if (disabled) {
        auto *px = static_cast<BYTE *>(bits);
        for (int i = 0; i < size * size; ++i) {
            BYTE *p = px + i * 4;
            int lum = (p[2] * 299 + p[1] * 587 + p[0] * 114) / 1000;
            p[0] = p[1] = p[2] = static_cast<BYTE>(lum * 35 / 100);
            p[3] = static_cast<BYTE>(p[3] * 35 / 100);
        }
    }
    return dib;
}

inline void draw(HDC dc, HBITMAP bmp, int x, int y, int size) {
    if (!bmp) return;
    HDC mem = CreateCompatibleDC(dc);
    HGDIOBJ old = SelectObject(mem, bmp);
    BLENDFUNCTION bf{AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
    AlphaBlend(dc, x, y, size, size, mem, 0, 0, size, size, bf);
    SelectObject(mem, old);
    DeleteDC(mem);
}

// Cache of rendered icons, keyed by kind/name/state/size.
class Cache {
public:
    HBITMAP tool(const std::string &name, int size, bool disabled = false) {
        std::string key = "t|" + name + "|" + std::to_string(size) + (disabled ? "|d" : "");
        unsigned ink = rgbOf(g_theme.p.MUTED);
        return get(key, [&] { return render(size, [&](Painter &d) { paintTool(d, name, ink); }, disabled); });
    }
    HBITMAP file(const std::string &category, const std::string &state, int size) {
        std::string key = "f|" + category + "|" + state + "|" + std::to_string(size);
        unsigned ring = rgbOf(g_theme.p.SURFACE);
        return get(key, [&] { return render(size, [&](Painter &d) { paintFile(d, category, state, ring); }); });
    }
    HBITMAP tree(const std::string &name, int size) {
        std::string key = "r|" + name + "|" + std::to_string(size);
        unsigned ink = rgbOf(g_theme.p.MUTED);
        return get(key, [&] { return render(size, [&](Painter &d) { paintTree(d, name, ink); }); });
    }
    void clear() {
        for (auto &kv : cache_) DeleteObject(kv.second);
        cache_.clear();
    }

private:
    HBITMAP get(const std::string &key, const std::function<HBITMAP()> &make) {
        auto it = cache_.find(key);
        if (it != cache_.end()) return it->second;
        HBITMAP b = make();
        cache_[key] = b;
        return b;
    }
    std::unordered_map<std::string, HBITMAP> cache_;
};

inline Cache g_cache;

}  // namespace icons
}  // namespace md
