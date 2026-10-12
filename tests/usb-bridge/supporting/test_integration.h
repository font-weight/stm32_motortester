#ifndef TEST_BRIDGE_INTEGRATION_H
#define TEST_BRIDGE_INTEGRATION_H
#include "mt_usb_bridge.h"
enum test_operation { OP_XFER, OP_FIFO, OP_OPEN, OP_STALL, OP_CLEAR, OP_ADDRESS, OP_STATUS, OP_SOF, OP_CLOSE, OP_COUNT };
extern int test_reject_open, test_reject_xfer;
void test_dcd_call(enum test_operation op);
void test_mount_hook(void);
void test_pump(void);
#endif
