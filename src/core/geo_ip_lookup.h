/*
	Copyright (C) 2003-2005 Daniel Muller, dan at verliba dot cz
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

#ifndef GEO_IP_LOOKUP_H
#define GEO_IP_LOOKUP_H

/**
 * @file geo_ip_lookup.h
 * @brief Lightweight MaxMind GeoIP lookup for the new core.
 *
 * Self-contained replacement for the old cMaxMindDB that has no
 * dependency on cServerDC.  Searches standard system paths for
 * .mmdb files at construction time.
 *
 * Mirrors the old core's GetCCC() behaviour: given an IP string,
 * returns country code, country name, and city.
 */

#include <string>
#include <mutex>
#include <memory>

struct MMDB_s;  // Forward declare from <maxminddb.h>

namespace nVerliHub {

/// Result of a single GeoIP lookup.
struct GeoIPResult {
    std::string country_code;  ///< Two-letter ISO 3166-1 (e.g. "US"), or "--"
    std::string country_name;  ///< English country name, or "--"
    std::string city;          ///< City name, or "--"
};

/**
 * Lightweight GeoIP lookup using MaxMind .mmdb databases.
 *
 * No dependency on cServerDC — reads databases from standard paths.
 * Thread-safe: lookup() can be called from any thread.
 */
class GeoIPLookup {
public:
    /**
     * Construct and load .mmdb databases.
     * @param mmdb_path  Optional custom directory containing .mmdb files.
     *                   If empty, searches standard system paths.
     */
    explicit GeoIPLookup(const std::string& mmdb_path = "");
    ~GeoIPLookup();

    // Non-copyable
    GeoIPLookup(const GeoIPLookup&) = delete;
    GeoIPLookup& operator=(const GeoIPLookup&) = delete;

    /**
     * Look up GeoIP data for an IP address string.
     *
     * Returns country code, country name, and city.
     * For private/local IPs returns special codes (L1, P1).
     * Thread-safe.
     *
     * @param ip  IPv4 or IPv6 address string
     * @return GeoIPResult with fields filled (or "--" on failure)
     */
    [[nodiscard]] GeoIPResult Lookup(const std::string& ip) const;

    /**
     * @return true if at least one database (country or city) was loaded.
     */
    [[nodiscard]] bool IsAvailable() const noexcept;

    /**
     * Reload databases from disk.
     */
    void Reload(const std::string& mmdb_path = "");

private:
    /// Try to open a country database, returns nullptr on failure
    MMDB_s* TryOpenCountryDB(const std::string& dir) const;

    /// Try to open a city database, returns nullptr on failure
    MMDB_s* TryOpenCityDB(const std::string& dir) const;

    /// Try to open a specific .mmdb file
    MMDB_s* TryOpenDB(const std::string& path) const;

    /// Close and free a database handle
    static void CloseDB(MMDB_s* db);

    /// Check if IP is a private/local address
    static bool IsPrivateIP(const std::string& ip);

    /// Check if IP is localhost
    static bool IsLocalIP(const std::string& ip);

    MMDB_s* m_country_db{nullptr};
    MMDB_s* m_city_db{nullptr};
    mutable std::mutex m_mutex;  ///< Protects DB handles during Reload()
};

}  // namespace nVerliHub

#endif  // GEO_IP_LOOKUP_H
