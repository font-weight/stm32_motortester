#ifndef MT_USB_BRIDGE_H
#define MT_USB_BRIDGE_H
#include "mt_usb_epoch.h"
#include "tusb.h"
#include "device/dcd.h"

/* Single foreground owner; IRQ producers never change the active actor.
 * enter/leave must save/restore the actual previous interrupt mask, support
 * nesting, and serialize every metadata access through the complete DCD call.
 * This interface alone supplies no target IRQ implementation. */
typedef uint32_t (*mt_usb_lock_fn)(void);
typedef void (*mt_usb_unlock_fn)(uint32_t previous);
typedef struct { uint32_t previous; unsigned entered; } mt_usb_scope;
enum { MT_USB_DROP, MT_USB_DEFER, MT_USB_DISPATCH };
void mt_usb_bridge_init(mt_usb_lock_fn enter, mt_usb_unlock_fn leave);
uint32_t mt_usb_bridge_lock(void);
void mt_usb_bridge_unlock(uint32_t previous);
/* The following producer/queue calls require the caller-held bridge lock. */
uint32_t mt_usb_bridge_ingress(const dcd_event_t *event);
void mt_usb_bridge_enqueued(uint32_t token, unsigned reset, unsigned accepted);
/* Direct peripheral fault ingress: takes its own guard, never queues an event
 * or calls controller/Stop. PMAOVR does not advance the epoch. A physical
 * BUS_RESET is required to clear poison; disconnect may also advance the
 * epoch but leaves it blocked. */
void mt_usb_bridge_pma_overrun(void);
uint32_t mt_usb_bridge_actor(void);
/* Snapshot and cleanup receipt never execute controller or USB callbacks.
 * cleaned is a caller assertion AFTER actual foreground controller/adapter
 * cleanup attempt, not evidence of successful hardware Stop. */
mt_usb_epoch mt_usb_bridge_snapshot(void);
void mt_usb_bridge_cleaned(uint32_t token);
unsigned mt_usb_bridge_dispatch(uint32_t token, unsigned reset, mt_usb_scope *scope);
int mt_usb_bridge_app_enter(mt_usb_scope *scope);
void mt_usb_bridge_leave(mt_usb_scope *scope);
void mt_usb_bridge_reset_done(uint32_t token);
int mt_usb_bridge_config_begin(uint8_t old_cfg, uint8_t new_cfg);
void mt_usb_bridge_config_done(uint8_t cfg, bool status_submitted);
/* Explicit shims, not linker --wrap (same-TU weak calls bypass that). */
bool mt_usb_bridge_xfer(uint8_t rhport, uint8_t ep, uint8_t *buf, uint16_t n, bool is_isr);
bool mt_usb_bridge_fifo(uint8_t rhport, uint8_t ep, tu_fifo_t *fifo, uint16_t n, bool is_isr);
bool mt_usb_bridge_open(uint8_t rhport, const tusb_desc_endpoint_t *ep);
void mt_usb_bridge_stall(uint8_t rhport, uint8_t ep);
void mt_usb_bridge_clear_stall(uint8_t rhport, uint8_t ep);
bool mt_usb_bridge_address(uint8_t rhport, uint8_t address);
void mt_usb_bridge_address_result(bool accepted);
void mt_usb_bridge_status(uint8_t rhport, const tusb_control_request_t *request);
void mt_usb_bridge_sof(uint8_t rhport, bool enabled);
void mt_usb_bridge_close_all(uint8_t rhport);
#endif
