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

	# Make sure proto files are generated before building test
	add_dependencies(test_nmdcpb libverlihub_so)

	add_test(NAME nmdcpb_unit_tests COMMAND test_nmdcpb)

	message(STATUS "[ OK ] NMDCpb C++ unit tests enabled")
else()
	message(STATUS "[ !! ] NMDCpb C++ unit tests disabled (no protobuf)")
endif()
