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

#include "geo_ip_lookup.h"

#include <maxminddb.h>
#include <cstring>
#include <cstdlib>
#include <iostream>
#include <sys/stat.h>
#include <arpa/inet.h>

namespace nVerliHub {

// Standard system paths where .mmdb files are commonly installed
static const char* const SEARCH_DIRS[] = {
    "/usr/share/GeoIP",
    "/usr/local/share/GeoIP",
    "/var/lib/GeoIP",
    "."
};
static constexpr size_t NUM_SEARCH_DIRS = sizeof(SEARCH_DIRS) / sizeof(SEARCH_DIRS[0]);

static bool FileExists(const char* path) {
    struct stat st;
    return stat(path, &st) == 0 && S_ISREG(st.st_mode);
}

// ============================================================================
// Construction / Destruction
// ============================================================================

GeoIPLookup::GeoIPLookup(const std::string& mmdb_path)
{
    Reload(mmdb_path);
}

GeoIPLookup::~GeoIPLookup()
{
    CloseDB(m_country_db);
    CloseDB(m_city_db);
}

// ============================================================================
// Public API
// ============================================================================

GeoIPResult GeoIPLookup::Lookup(const std::string& ip) const
{
    GeoIPResult result{"--", "--", "--"};

    if (ip.empty())
        return result;

    // Localhost
    if (IsLocalIP(ip)) {
        result.country_code = "L1";
        result.country_name = "Local Network";
        result.city = "Local Network";
        return result;
    }

    // Private IP ranges
    if (IsPrivateIP(ip)) {
        result.country_code = "P1";
        result.country_name = "Private Network";
        result.city = "Private Network";
        return result;
    }

    std::lock_guard<std::mutex> lock(m_mutex);

    // Use city DB first (has country+city), fall back to country DB
    MMDB_s* db = m_city_db ? m_city_db : m_country_db;
    if (!db)
        return result;

    int gai_err = 0, mmdb_err = 0;
    MMDB_lookup_result_s dat = MMDB_lookup_string(db, ip.c_str(), &gai_err, &mmdb_err);

    if (gai_err != 0 || mmdb_err != MMDB_SUCCESS || !dat.found_entry)
        return result;

    MMDB_entry_data_s ent;

    // Country code
    if ((MMDB_get_value(&dat.entry, &ent, "country", "iso_code", nullptr) == MMDB_SUCCESS ||
         MMDB_get_value(&dat.entry, &ent, "registered_country", "iso_code", nullptr) == MMDB_SUCCESS)
        && ent.has_data && ent.type == MMDB_DATA_TYPE_UTF8_STRING && ent.data_size > 0)
    {
        result.country_code.assign(ent.utf8_string, ent.data_size);
    }

    // Country name (English)
    if ((MMDB_get_value(&dat.entry, &ent, "country", "names", "en", nullptr) == MMDB_SUCCESS ||
         MMDB_get_value(&dat.entry, &ent, "registered_country", "names", "en", nullptr) == MMDB_SUCCESS)
        && ent.has_data && ent.type == MMDB_DATA_TYPE_UTF8_STRING && ent.data_size > 0)
    {
        result.country_name.assign(ent.utf8_string, ent.data_size);
    }

    // City name (English)
    if ((MMDB_get_value(&dat.entry, &ent, "city", "names", "en", nullptr) == MMDB_SUCCESS)
        && ent.has_data && ent.type == MMDB_DATA_TYPE_UTF8_STRING && ent.data_size > 0)
    {
        result.city.assign(ent.utf8_string, ent.data_size);
    }

    return result;
}

bool GeoIPLookup::IsAvailable() const noexcept
{
    return m_country_db != nullptr || m_city_db != nullptr;
}

void GeoIPLookup::Reload(const std::string& mmdb_path)
{
    std::lock_guard<std::mutex> lock(m_mutex);

    // Close existing handles
    CloseDB(m_country_db);
    m_country_db = nullptr;
    CloseDB(m_city_db);
    m_city_db = nullptr;

    if (!mmdb_path.empty()) {
        // Custom path specified — search there only
        m_country_db = TryOpenCountryDB(mmdb_path);
        m_city_db = TryOpenCityDB(mmdb_path);
    } else {
        // Search standard system paths
        for (size_t i = 0; i < NUM_SEARCH_DIRS; ++i) {
            if (!m_country_db)
                m_country_db = TryOpenCountryDB(SEARCH_DIRS[i]);
            if (!m_city_db)
                m_city_db = TryOpenCityDB(SEARCH_DIRS[i]);
            if (m_country_db && m_city_db)
                break;
        }

        // Also check GEOIP_DB_PATH env var
        const char* env = std::getenv("GEOIP_DB_PATH");
        if (env && *env) {
            if (!m_country_db)
                m_country_db = TryOpenCountryDB(env);
            if (!m_city_db)
                m_city_db = TryOpenCityDB(env);
        }
    }

    if (m_country_db || m_city_db) {
        std::cout << "GeoIPLookup: databases loaded ("
                  << (m_country_db ? "country" : "")
                  << (m_country_db && m_city_db ? "+" : "")
                  << (m_city_db ? "city" : "")
                  << ")" << std::endl;
    }
}

// ============================================================================
// Database file loading
// ============================================================================

MMDB_s* GeoIPLookup::TryOpenCountryDB(const std::string& dir) const
{
    const char* names[] = {
        "GeoIP2-Country.mmdb",
        "GeoLite2-Country.mmdb"
    };
    for (auto name : names) {
        std::string path = dir + "/" + name;
        MMDB_s* db = TryOpenDB(path);
        if (db) return db;
    }
    return nullptr;
}

MMDB_s* GeoIPLookup::TryOpenCityDB(const std::string& dir) const
{
    const char* names[] = {
        "GeoIP2-City.mmdb",
        "GeoLite2-City.mmdb"
    };
    for (auto name : names) {
        std::string path = dir + "/" + name;
        MMDB_s* db = TryOpenDB(path);
        if (db) return db;
    }
    return nullptr;
}

MMDB_s* GeoIPLookup::TryOpenDB(const std::string& path) const
{
    if (!FileExists(path.c_str()))
        return nullptr;

    MMDB_s* db = static_cast<MMDB_s*>(std::malloc(sizeof(MMDB_s)));
    if (!db)
        return nullptr;

    int status = MMDB_open(path.c_str(), MMDB_MODE_MMAP, db);
    if (status != MMDB_SUCCESS) {
        std::free(db);
        return nullptr;
    }

    std::cout << "GeoIPLookup: loaded " << path << std::endl;
    return db;
}

void GeoIPLookup::CloseDB(MMDB_s* db)
{
    if (db) {
        MMDB_close(db);
        std::free(db);
    }
}

// ============================================================================
// IP classification helpers
// ============================================================================

bool GeoIPLookup::IsLocalIP(const std::string& ip)
{
    return ip.substr(0, 4) == "127." || ip == "::1" || ip == "0.0.0.0";
}

bool GeoIPLookup::IsPrivateIP(const std::string& ip)
{
    // Quick check for IPv4 private ranges
    // 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16
    struct in_addr addr;
    if (inet_pton(AF_INET, ip.c_str(), &addr) == 1) {
        uint32_t n = ntohl(addr.s_addr);
        if ((n >= 0x0A000000u && n <= 0x0AFFFFFFu) ||  // 10.0.0.0/8
            (n >= 0xAC100000u && n <= 0xAC1FFFFFu) ||  // 172.16.0.0/12
            (n >= 0xC0A80000u && n <= 0xC0A8FFFFu) ||  // 192.168.0.0/16
            (n >= 0xA9FE0000u && n <= 0xA9FEFFFFu))    // 169.254.0.0/16
            return true;
    }

    // IPv6 ULA (fc00::/7) and link-local (fe80::/10)
    if (ip.size() >= 4) {
        if (ip[0] == 'f' || ip[0] == 'F') {
            char c1 = ip[1];
            if (c1 == 'c' || c1 == 'C' || c1 == 'd' || c1 == 'D' ||
                c1 == 'e' || c1 == 'E')
                return true;
        }
    }

    return false;
}

}  // namespace nVerliHub
