# The main component may use every ESP-IDF component (no REQUIRES list).
idf_component_register(SRCS "main.c"
                       INCLUDE_DIRS ".")
