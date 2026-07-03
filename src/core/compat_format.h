/*
	Copyright (C) 2006-2026 Verlihub Team, info at verlihub dot net

	Verlihub is free software; You can redistribute it
	and modify it under the terms of the GNU General
	Public License as published by the Free Software
	Foundation, either version 3 of the license, or at
	your option any later version.

	Verlihub is distributed in the hope that it will be
	useful, but without any warranty, without even the
	implied warranty of merchantability or fitness for
	a particular purpose. See the GNU General Public
	License for more details.

	Please see http://www.gnu.org/licenses/ for a copy
	of the GNU General Public License.
*/

#ifndef VH_COMPAT_FORMAT_H
#define VH_COMPAT_FORMAT_H

/**
 * @file compat_format.h
 * @brief Compatibility shim for std::format (C++20 P0645).
 *
 * GCC added <format> support in GCC 13. For older compilers (GCC 11/12)
 * we provide a lightweight fallback using std::ostringstream.
 *
 * Usage:
 *   #include "compat_format.h"
 *   std::string s = vh::fmt("Listening on {}:{}", ip, port);
 *
 * When std::format is available (GCC 13+, Clang 17+, MSVC 19.29+),
 * vh::fmt() is a thin wrapper around std::format. Otherwise it uses
 * an ostringstream-based implementation that handles {} placeholders.
 */

// Check for std::format support via feature-test macro
#if __has_include(<version>)
#include <version>
#endif

#if defined(__cpp_lib_format) && __cpp_lib_format >= 202110L
// ============================================================================
// Native std::format path (GCC 13+, Clang 17+, MSVC 19.29+)
// ============================================================================
#define VH_HAS_STD_FORMAT 1
#include <format>
#include <string>

namespace vh {

/// Thin wrapper around std::format
template<typename... Args>
inline std::string fmt(std::format_string<Args...> fstr, Args&&... args) {
    return std::format(fstr, std::forward<Args>(args)...);
}

} // namespace vh

#else
// ============================================================================
// Fallback path for compilers without <format> (GCC 11/12)
// ============================================================================
#define VH_HAS_STD_FORMAT 0
#include <string>
#include <sstream>

namespace vh {

namespace detail {

/// Write one argument into the stream at the next {} placeholder
inline void format_arg(std::ostringstream& oss, const std::string& fmt_str,
                       size_t& pos) {
    // No more args — append the rest of the format string
    oss << fmt_str.substr(pos);
    pos = fmt_str.size();
}

template<typename T, typename... Rest>
void format_arg(std::ostringstream& oss, const std::string& fmt_str,
                size_t& pos, const T& val, const Rest&... rest) {
    size_t brace = fmt_str.find("{}", pos);
    if (brace == std::string::npos) {
        // No more placeholders — append the rest
        oss << fmt_str.substr(pos);
        pos = fmt_str.size();
        return;
    }
    // Append text before the placeholder, then the value
    oss << fmt_str.substr(pos, brace - pos) << val;
    pos = brace + 2;
    format_arg(oss, fmt_str, pos, rest...);
}

} // namespace detail

/// Format string with {} placeholders (fallback for pre-GCC-13)
template<typename... Args>
inline std::string fmt(const std::string& fmt_str, const Args&... args) {
    std::ostringstream oss;
    size_t pos = 0;
    detail::format_arg(oss, fmt_str, pos, args...);
    // Append any trailing text after the last placeholder
    if (pos < fmt_str.size()) {
        oss << fmt_str.substr(pos);
    }
    return oss.str();
}

/// Zero-arg overload — just return the format string as-is
inline std::string fmt(const std::string& s) {
    return s;
}

} // namespace vh

#endif // __cpp_lib_format

#endif // VH_COMPAT_FORMAT_H
