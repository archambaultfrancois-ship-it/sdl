package org.sdlchain;

import java.io.IOException;
import java.math.BigDecimal;
import java.util.*;

/** Small strict JSON reader for configuration and CENV administrative replies. */
public final class Json {
    private final String text; private int pos;
    private Json(String text) { this.text = text; }
    public static Object parse(String text) throws IOException {
        if (text == null || text.length() > 1024 * 1024) throw new IOException("JSON exceeds limit");
        Json p = new Json(text); Object value = p.value(0); p.space();
        if (p.pos != text.length()) throw p.error(); return value;
    }
    private IOException error() { return new IOException("invalid JSON at " + pos); }
    private void space() { while (pos < text.length() && " \t\r\n".indexOf(text.charAt(pos)) >= 0) ++pos; }
    private boolean take(char c) { space(); if (pos < text.length() && text.charAt(pos) == c) { ++pos; return true; } return false; }
    private String string() throws IOException {
        if (!take('"')) throw error(); StringBuilder s = new StringBuilder();
        while (pos < text.length()) {
            char c = text.charAt(pos++);
            if (c == '"') { Component.utf8(s.toString()); return s.toString(); }
            if (c < 32) throw error();
            if (c == '\\') {
                if (pos == text.length()) throw error(); c = text.charAt(pos++);
                switch (c) {
                case '"': case '\\': case '/': break;
                case 'b': c = '\b'; break; case 'f': c = '\f'; break; case 'n': c = '\n'; break;
                case 'r': c = '\r'; break; case 't': c = '\t'; break;
                case 'u':
                    if (pos + 4 > text.length()) throw error();
                    int n = 0;
                    for (int i = 0; i < 4; ++i) { int digit = Character.digit(text.charAt(pos++), 16); if (digit < 0) throw error(); n = n * 16 + digit; }
                    c = (char)n; break;
                default: throw error();
                }
            }
            s.append(c);
        }
        throw error();
    }
    private Object value(int depth) throws IOException {
        if (depth > 64) throw error(); space(); if (pos == text.length()) throw error();
        if (text.charAt(pos) == '"') return string();
        if (take('{')) {
            Map<String,Object> result = new LinkedHashMap<String,Object>(); if (take('}')) return result;
            do { String key = string(); if (!take(':') || result.containsKey(key)) throw error(); result.put(key, value(depth + 1)); } while (take(','));
            if (!take('}')) throw error(); return result;
        }
        if (take('[')) {
            List<Object> result = new ArrayList<Object>(); if (take(']')) return result;
            do { result.add(value(depth + 1)); } while (take(',')); if (!take(']')) throw error(); return result;
        }
        String[] literals = {"true", "false", "null"}; Object[] values = {Boolean.TRUE, Boolean.FALSE, null};
        for (int i = 0; i < literals.length; ++i) if (text.startsWith(literals[i], pos)) { pos += literals[i].length(); return values[i]; }
        int start = pos;
        while (pos < text.length() && "0123456789+-.eE".indexOf(text.charAt(pos)) >= 0) ++pos;
        String number = text.substring(start, pos);
        if (!number.matches("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?")) throw error();
        return new BigDecimal(number);
    }
    public static String quote(String value) throws IOException {
        Component.utf8(value); StringBuilder s = new StringBuilder("\"");
        for (int i = 0; i < value.length(); ++i) {
            char c = value.charAt(i);
            if (c == '"' || c == '\\') s.append('\\').append(c);
            else if (c < 32) s.append(String.format(java.util.Locale.ROOT, "\\u%04x", (int)c));
            else s.append(c);
        }
        return s.append('"').toString();
    }
}
