# NMDCpb C++ unit tests
# Only built when protobuf is available

if(Protobuf_FOUND)
	set(NMDCPB_GENERATED_DIR "${CMAKE_BINARY_DIR}/nmdcpb_generated")

	add_executable(test_nmdcpb
		test_nmdcpb.cpp
		${CMAKE_SOURCE_DIR}/src/cpbtranslate.cpp
		${NMDCPB_GENERATED_DIR}/nmdcpb.pb.cc
	)

	target_compile_definitions(test_nmdcpb PRIVATE WITH_NMDCPB)

	target_include_directories(test_nmdcpb PRIVATE
		${CMAKE_SOURCE_DIR}/src
		${NMDCPB_GENERATED_DIR}
		${Protobuf_INCLUDE_DIRS}
	)

	target_link_libraries(test_nmdcpb protobuf::libprotobuf)

	# Relay manager unit tests (unity build - includes crelay.cpp directly)
	add_executable(test_relay
		test_relay.cpp
	)

	target_compile_definitions(test_relay PRIVATE WITH_NMDCPB)

	target_include_directories(test_relay PRIVATE
		${CMAKE_SOURCE_DIR}/src
	)

	target_link_libraries(test_relay pthread)

	# Make sure proto files are generated before building tests
	add_dependencies(test_nmdcpb libverlihub_so)

	add_test(NAME nmdcpb_unit_tests COMMAND test_nmdcpb)
	add_test(NAME relay_unit_tests COMMAND test_relay)

	message(STATUS "[ OK ] NMDCpb C++ unit tests enabled")
else()
	message(STATUS "[ !! ] NMDCpb C++ unit tests disabled (no protobuf)")
endif()
