/* A native executable for launch-assembly tests, never an inference server. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (getenv("TEST_NATIVE_REJECT_COMMAND") != NULL) {
        return 2;
    }
    if (argc < 2 || strcmp(argv[argc - 1], "--help") != 0) {
        return 1;
    }
    puts("--model --ctx-size");
    return 0;
}
