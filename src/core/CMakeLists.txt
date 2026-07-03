#
#	Copyright (C) 2003-2005 Daniel Muller, dan at verliba dot cz
#	Copyright (C) 2006-2026 Verlihub Team, info at verlihub dot net
#
#	Verlihub is free software; You can redistribute it
#	and modify it under the terms of the GNU General
#	Public License as published by the Free Software
#	Foundation, either version 3 of the license, or at
#	your option any later version.
#
#	Verlihub is distributed in the hope that it will be
#	useful, but without any warranty, without even the
#	implied warranty of merchantability or fitness for
#	a particular purpose. See the GNU General Public
#	License for more details.
#
#	Please see http://www.gnu.org/licenses/ for a copy
#	of the GNU General Public License.
#

# CMakeLists.txt for new C++20 core library
#
# This library contains the refactored, thread-safe core components:
# - HubContext: Central context replacing all global state
# - ThreadSafeCollections: Lock-free and mutex-protected containers
# - Protocol handling (to be added)

# Option to build core library tests
option(BUILD_CORE_TESTS "Build C++20 core library unit tests" ON)

set(CORE_SOURCES
    hub_context.cpp
    thread_safe_collections.cpp
    nmdc_protocol.cpp
    nmdc_hub_server.cpp
    geo_ip_lookup.cpp
)

set(CORE_HEADERS
    hub_context.h
    thread_safe_collections.h
    nmdc_protocol.h
    nmdc_hub_server.h
    geo_ip_lookup.h
    compat_format.h
)

# Create the core library (static for linking into main app and SWIG module)
add_library(verlihub_core_lib STATIC ${CORE_SOURCES})

# Set C++20 requirement
target_compile_features(verlihub_core_lib PUBLIC cxx_std_20)

# Include directories
target_include_directories(verlihub_core_lib
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}
        ${CMAKE_SOURCE_DIR}/src
        ${CMAKE_BINARY_DIR}
)

# Additional compiler flags for C++20 features
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_compile_options(verlihub_core_lib PRIVATE
        -fconcepts-diagnostics-depth=2
    )
endif()

# Enable position-independent code (needed for shared library/SWIG)
set_target_properties(verlihub_core_lib PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)

# Link against pthread on Unix and the main verlihub shared library.
# NMDCHubServer inherits from cAsyncSocketServer which lives in
# libverlihub_so. We only use the socket infrastructure (no MySQL).
if(UNIX)
    target_link_libraries(verlihub_core_lib PUBLIC pthread)
endif()
target_link_libraries(verlihub_core_lib PUBLIC libverlihub_so)

# Install headers (system-wide build only, not for Python wheel)
if(NOT PYTHON_WHEEL_BUILD)
    install(FILES ${CORE_HEADERS}
        DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/${PROJECT_NAME}/core
    )
endif()

# For IDE support - mark headers
source_group("Headers" FILES ${CORE_HEADERS})
source_group("Sources" FILES ${CORE_SOURCES})

# =============================================================================
# Unit Tests
# =============================================================================

if(BUILD_CORE_TESTS)
    find_package(GTest)
    
    if(GTest_FOUND)
        message(STATUS "[ OK ] Building core library tests")
        
        # ThreadSafeCollections tests
        add_executable(test_thread_safe_collections 
            tests/test_thread_safe_collections.cpp
        )
        target_link_libraries(test_thread_safe_collections 
            GTest::gtest 
            GTest::gtest_main 
            verlihub_core_lib
            pthread
        )
        target_compile_definitions(test_thread_safe_collections PRIVATE 
            BUILD_DIR="${CMAKE_BINARY_DIR}"
        )
        target_compile_features(test_thread_safe_collections PRIVATE cxx_std_20)
        
        # HubContext tests
        add_executable(test_hub_context 
            tests/test_hub_context.cpp
        )
        target_link_libraries(test_hub_context 
            GTest::gtest 
            GTest::gtest_main 
            verlihub_core_lib
            pthread
        )
        target_compile_definitions(test_hub_context PRIVATE 
            BUILD_DIR="${CMAKE_BINARY_DIR}"
        )
        target_compile_features(test_hub_context PRIVATE cxx_std_20)
        
        # NMDCProtocol tests
        add_executable(test_nmdc_protocol
            tests/test_nmdc_protocol.cpp
        )
        target_link_libraries(test_nmdc_protocol
            GTest::gtest
            GTest::gtest_main
            verlihub_core_lib
            pthread
        )
        target_compile_definitions(test_nmdc_protocol PRIVATE
            BUILD_DIR="${CMAKE_BINARY_DIR}"
        )
        target_compile_features(test_nmdc_protocol PRIVATE cxx_std_20)

        # NMDCHubServer tests
        add_executable(test_nmdc_hub_server
            tests/test_nmdc_hub_server.cpp
        )
        target_link_libraries(test_nmdc_hub_server
            GTest::gtest
            GTest::gtest_main
            verlihub_core_lib
            pthread
        )
        target_compile_definitions(test_nmdc_hub_server PRIVATE
            BUILD_DIR="${CMAKE_BINARY_DIR}"
        )
        target_compile_features(test_nmdc_hub_server PRIVATE cxx_std_20)

        # GeoIPLookup tests
        add_executable(test_geo_ip_lookup
            tests/test_geo_ip_lookup.cpp
        )
        target_link_libraries(test_geo_ip_lookup
            GTest::gtest
            GTest::gtest_main
            verlihub_core_lib
            pthread
        )
        target_compile_definitions(test_geo_ip_lookup PRIVATE
            BUILD_DIR="${CMAKE_BINARY_DIR}"
        )
        target_compile_features(test_geo_ip_lookup PRIVATE cxx_std_20)

        # Register tests with CTest
        add_test(NAME ThreadSafeCollectionsTests 
            COMMAND test_thread_safe_collections
        )
        set_tests_properties(ThreadSafeCollectionsTests PROPERTIES 
            LABELS "core;unit"
            TIMEOUT 60
        )
        
        add_test(NAME HubContextTests 
            COMMAND test_hub_context
        )
        set_tests_properties(HubContextTests PROPERTIES 
            LABELS "core;unit"
            TIMEOUT 120
        )

        add_test(NAME NMDCProtocolTests
            COMMAND test_nmdc_protocol
        )
        set_tests_properties(NMDCProtocolTests PROPERTIES
            LABELS "core;unit"
            TIMEOUT 60
        )

        add_test(NAME NMDCHubServerTests
            COMMAND test_nmdc_hub_server
        )
        set_tests_properties(NMDCHubServerTests PROPERTIES
            LABELS "core;unit"
            TIMEOUT 60
        )

        add_test(NAME GeoIPLookupTests
            COMMAND test_geo_ip_lookup
        )
        set_tests_properties(GeoIPLookupTests PROPERTIES
            LABELS "core;unit"
            TIMEOUT 60
        )
        
        message(STATUS "[ OK ] Core tests configured: ThreadSafeCollections, HubContext, NMDCProtocol, NMDCHubServer, GeoIPLookup")
    else()
        message(STATUS "[ !! ] GTest not found; skipping core library tests")
    endif()
else()
    message(STATUS "[ .. ] Core library tests disabled (use -DBUILD_CORE_TESTS=ON to enable)")
endif()
