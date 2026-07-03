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

#include <gtest/gtest.h>
#include <thread>
#include <vector>

#include "../geo_ip_lookup.h"

using namespace nVerliHub;

// =============================================================================
// GeoIPResult struct tests
// =============================================================================

TEST(GeoIPResultTest, DefaultValues) {
    GeoIPResult r;
    // Default-constructed strings are empty
    EXPECT_TRUE(r.country_code.empty());
    EXPECT_TRUE(r.country_name.empty());
    EXPECT_TRUE(r.city.empty());
}

// =============================================================================
// GeoIPLookup construction
// =============================================================================

class GeoIPLookupTest : public ::testing::Test {
protected:
    // Constructed with a non-existent path so no databases load
    GeoIPLookup geo{"/nonexistent_geoip_path_for_test"};
};

TEST_F(GeoIPLookupTest, NoDBsAvailable) {
    EXPECT_FALSE(geo.IsAvailable());
}

// =============================================================================
// Localhost detection
// =============================================================================

TEST_F(GeoIPLookupTest, Lookup_Localhost_127_0_0_1) {
    auto r = geo.Lookup("127.0.0.1");
    EXPECT_EQ("L1", r.country_code);
    EXPECT_EQ("Local Network", r.country_name);
    EXPECT_EQ("Local Network", r.city);
}

TEST_F(GeoIPLookupTest, Lookup_Localhost_127_x) {
    auto r = geo.Lookup("127.5.6.7");
    EXPECT_EQ("L1", r.country_code);
}

TEST_F(GeoIPLookupTest, Lookup_Localhost_IPv6) {
    auto r = geo.Lookup("::1");
    EXPECT_EQ("L1", r.country_code);
    EXPECT_EQ("Local Network", r.country_name);
}

TEST_F(GeoIPLookupTest, Lookup_Localhost_0000) {
    auto r = geo.Lookup("0.0.0.0");
    EXPECT_EQ("L1", r.country_code);
}

// =============================================================================
// Private IP detection (RFC 1918 + link-local)
// =============================================================================

TEST_F(GeoIPLookupTest, Lookup_Private_10_x) {
    auto r = geo.Lookup("10.0.0.1");
    EXPECT_EQ("P1", r.country_code);
    EXPECT_EQ("Private Network", r.country_name);
    EXPECT_EQ("Private Network", r.city);
}

TEST_F(GeoIPLookupTest, Lookup_Private_10_255) {
    auto r = geo.Lookup("10.255.255.255");
    EXPECT_EQ("P1", r.country_code);
}

TEST_F(GeoIPLookupTest, Lookup_Private_172_16) {
    auto r = geo.Lookup("172.16.0.1");
    EXPECT_EQ("P1", r.country_code);
}

TEST_F(GeoIPLookupTest, Lookup_Private_172_31) {
    auto r = geo.Lookup("172.31.255.255");
    EXPECT_EQ("P1", r.country_code);
}

TEST_F(GeoIPLookupTest, Lookup_Private_192_168) {
    auto r = geo.Lookup("192.168.1.1");
    EXPECT_EQ("P1", r.country_code);
}

TEST_F(GeoIPLookupTest, Lookup_Private_169_254) {
    auto r = geo.Lookup("169.254.0.1");
    EXPECT_EQ("P1", r.country_code);
}

// IPv6 ULA (fc00::/7)
TEST_F(GeoIPLookupTest, Lookup_Private_IPv6_ULA) {
    auto r = geo.Lookup("fc00::1");
    EXPECT_EQ("P1", r.country_code);
}

TEST_F(GeoIPLookupTest, Lookup_Private_IPv6_ULA_fd) {
    auto r = geo.Lookup("fd12:3456:789a::1");
    EXPECT_EQ("P1", r.country_code);
}

// IPv6 link-local (fe80::/10)
TEST_F(GeoIPLookupTest, Lookup_Private_IPv6_LinkLocal) {
    auto r = geo.Lookup("fe80::1");
    EXPECT_EQ("P1", r.country_code);
}

// =============================================================================
// Non-private IPs that should NOT match private ranges
// =============================================================================

TEST_F(GeoIPLookupTest, Lookup_NotPrivate_172_32) {
    // 172.32.x.x is NOT private
    auto r = geo.Lookup("172.32.0.1");
    EXPECT_NE("P1", r.country_code);
    EXPECT_NE("L1", r.country_code);
    // Without a database, should return "--"
    EXPECT_EQ("--", r.country_code);
}

TEST_F(GeoIPLookupTest, Lookup_NotPrivate_11_x) {
    auto r = geo.Lookup("11.0.0.1");
    EXPECT_EQ("--", r.country_code);
}

TEST_F(GeoIPLookupTest, Lookup_NotPrivate_8_8_8_8) {
    auto r = geo.Lookup("8.8.8.8");
    EXPECT_EQ("--", r.country_code);
}

// =============================================================================
// Edge cases
// =============================================================================

TEST_F(GeoIPLookupTest, Lookup_EmptyString) {
    auto r = geo.Lookup("");
    EXPECT_EQ("--", r.country_code);
    EXPECT_EQ("--", r.country_name);
    EXPECT_EQ("--", r.city);
}

TEST_F(GeoIPLookupTest, Lookup_PublicIP_NoDB) {
    // Public IP with no database loaded returns defaults
    auto r = geo.Lookup("1.1.1.1");
    EXPECT_EQ("--", r.country_code);
    EXPECT_EQ("--", r.country_name);
    EXPECT_EQ("--", r.city);
}

// =============================================================================
// Reload with non-existent path keeps no databases
// =============================================================================

TEST_F(GeoIPLookupTest, Reload_NonexistentPath) {
    geo.Reload("/another/nonexistent/path");
    EXPECT_FALSE(geo.IsAvailable());
}

TEST_F(GeoIPLookupTest, Reload_EmptyString_NoSystemDBs) {
    // Will search system paths; most test machines won't have them
    // but it should not crash
    geo.Reload("");
    // We don't assert IsAvailable because it depends on the environment
}

// =============================================================================
// Thread safety - concurrent lookups should not crash
// =============================================================================

TEST_F(GeoIPLookupTest, ConcurrentLookups) {
    constexpr int NUM_THREADS = 8;
    constexpr int ITERATIONS = 100;

    std::vector<std::thread> threads;
    threads.reserve(NUM_THREADS);

    for (int t = 0; t < NUM_THREADS; ++t) {
        threads.emplace_back([this, t]() {
            for (int i = 0; i < ITERATIONS; ++i) {
                // Mix of IP types to hit all code paths
                (void)geo.Lookup("127.0.0.1");
                (void)geo.Lookup("10.0.0.1");
                (void)geo.Lookup("192.168.1.1");
                (void)geo.Lookup("8.8.8.8");
                (void)geo.Lookup("");
                (void)geo.Lookup("::1");
                (void)geo.Lookup("fe80::1");
            }
        });
    }

    for (auto& th : threads) {
        th.join();
    }
    // No crash or data race = pass
}

// =============================================================================
// Multiple construction / destruction (no leaks / double-free)
// =============================================================================

TEST(GeoIPLookupLifecycleTest, CreateDestroy) {
    for (int i = 0; i < 5; ++i) {
        GeoIPLookup g("/nonexistent");
        EXPECT_FALSE(g.IsAvailable());
        auto r = g.Lookup("127.0.0.1");
        EXPECT_EQ("L1", r.country_code);
    }
}

TEST(GeoIPLookupLifecycleTest, ReloadMultipleTimes) {
    GeoIPLookup g("/nonexistent");
    for (int i = 0; i < 10; ++i) {
        g.Reload("/nonexistent_" + std::to_string(i));
        EXPECT_FALSE(g.IsAvailable());
    }
}
