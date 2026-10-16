# Project wrapper used for every build (AI-Driven Embedded Programmer).
# Only main/main.c changes between builds, so rebuilds are fast.
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(app)
