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

# CMakeLists.txt for SWIG Python bindings

find_package(SWIG 4.0 REQUIRED)
include(UseSWIG)

find_package(Python3 COMPONENTS Interpreter Development REQUIRED)

# Verify Python version >= 3.10 for SQLModel compatibility
if(Python3_VERSION VERSION_LESS "3.10")
    message(FATAL_ERROR "Python 3.10 or higher required, found ${Python3_VERSION}")
endif()

message(STATUS "Python ${Python3_VERSION} found: ${Python3_EXECUTABLE}")
message(STATUS "Python include dirs: ${Python3_INCLUDE_DIRS}")
message(STATUS "Python libraries: ${Python3_LIBRARIES}")

# Set SWIG properties
set(CMAKE_SWIG_FLAGS -threads -Wall -relativeimport)

# SWIG interface file
set_source_files_properties(
    verlihub_core.i
    PROPERTIES
        CPLUSPLUS ON
        SWIG_MODULE_NAME verlihub_core
        USE_TARGET_INCLUDE_DIRECTORIES TRUE
)

# Create the SWIG module
swig_add_library(verlihub_core
    TYPE SHARED
    LANGUAGE python
    OUTPUT_DIR ${CMAKE_BINARY_DIR}/python/verlihub
    OUTFILE_DIR ${CMAKE_BINARY_DIR}/swig
    SOURCES verlihub_core.i
)

# Set include directories for the SWIG wrapper
target_include_directories(verlihub_core
    PRIVATE
        ${CMAKE_SOURCE_DIR}/src
        ${CMAKE_BINARY_DIR}
        ${Python3_INCLUDE_DIRS}
)

# Link against Python and the core library
# Note: We need to link against libverlihub to access plugin management symbols
target_link_libraries(verlihub_core
    PRIVATE
        verlihub_core_lib  # The refactored core library
        libverlihub_so     # Main verlihub shared library for plugin management
        Python3::Python
)

# Set output name (remove lib prefix)
set_target_properties(verlihub_core PROPERTIES
    PREFIX ""
    OUTPUT_NAME "_verlihub_core"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/python/verlihub"
    # RPATH: look for libverlihub.so in the same directory as _verlihub_core.so
    INSTALL_RPATH "$ORIGIN"
    BUILD_WITH_INSTALL_RPATH TRUE
)

# Platform-specific suffix
if(WIN32)
    set_target_properties(verlihub_core PROPERTIES SUFFIX ".pyd")
else()
    set_target_properties(verlihub_core PROPERTIES SUFFIX ".so")
endif()

# Copy the generated Python file to output directory
add_custom_command(TARGET verlihub_core POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        ${CMAKE_BINARY_DIR}/python/verlihub/verlihub_core.py
        ${CMAKE_BINARY_DIR}/swig/verlihub_core.py
    COMMENT "SWIG Python module built: verlihub_core"
)

# Install the Python module (system-wide, for non-wheel builds)
install(TARGETS verlihub_core
    LIBRARY DESTINATION ${Python3_SITEARCH}/verlihub
)

install(FILES 
    ${CMAKE_BINARY_DIR}/python/verlihub/verlihub_core.py
    DESTINATION ${Python3_SITEARCH}/verlihub
)

# Install for Python wheel (scikit-build-core picks up this component)
# Note: wheel.install-dir = "verlihub" in pyproject.toml already maps
# CMAKE_INSTALL_PREFIX → verlihub/ in the wheel, so DESTINATION must be "."
# to avoid a doubled verlihub/verlihub/ path.
install(TARGETS verlihub_core
    COMPONENT PythonModule
    LIBRARY DESTINATION .
)

install(FILES
    ${CMAKE_BINARY_DIR}/python/verlihub/verlihub_core.py
    COMPONENT PythonModule
    DESTINATION .
)

# Note: libverlihub.so is installed into the wheel from src/CMakeLists.txt
# (install(TARGETS) must be in the same directory that defined the target)

# Add Python SWIG wrapper tests
# Ensure pytest is available
execute_process(
    COMMAND ${Python3_EXECUTABLE} -c "import pytest"
    RESULT_VARIABLE PYTEST_FOUND
    OUTPUT_QUIET ERROR_QUIET
)

if(PYTEST_FOUND EQUAL 0)
    add_test(NAME SwigWrapperTests
        COMMAND ${Python3_EXECUTABLE} -m pytest
            ${CMAKE_SOURCE_DIR}/python/tests/test_verlihub_core.py
            -v --tb=short --noconftest -p no:asyncio
        WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
    )
    set_tests_properties(SwigWrapperTests PROPERTIES
        LABELS "swig;python;unit"
        ENVIRONMENT "PYTHONPATH=${CMAKE_BINARY_DIR}/python:$ENV{PYTHONPATH}"
    )
else()
    message(STATUS "pytest not found, skipping SWIG Python tests")
endif()
