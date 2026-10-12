/* Target ABI symbols only. NEVER link this probe into firmware. */
#include <stddef.h>
#include "mt_adc_control.h"
#include "mt_stream.h"
/* Session, TX, link and acquisition are all owned by control. No duplicates.
 * block in mt_adc_control_service is automatic storage, counted by .su. */
typedef struct {
    mt_control control;
    mt_adc_dma_t driver;
    mt_adc_control binding;
    mt_stream rx_stream;
    mt_wire_message rx_message;
} resource_owner_model;
_Static_assert(sizeof(((mt_control *)0)->session)==sizeof(mt_session), "embedded session");
_Static_assert(sizeof(((mt_control *)0)->tx)==sizeof(mt_tx), "embedded TX");
_Static_assert(sizeof(((mt_control *)0)->link)==sizeof(mt_capture_link), "embedded link");
_Static_assert(sizeof(((mt_capture_link *)0)->acquisition)==sizeof(mt_acq_t), "embedded acquisition");
_Static_assert(sizeof(((mt_adc_dma_t *)0)->dma_samples)==512, "one DMA ring");
_Static_assert(sizeof(((mt_adc_dma_t *)0)->snapshot)==256, "one snapshot");
_Static_assert(sizeof(mt_adc_dma_state_t)==4, "explicit full-width enums");
_Static_assert(sizeof(mt_adc_dma_fault_t)==4, "explicit full-width enums");
unsigned char sizeof_mt_control[sizeof(mt_control)];
unsigned char alignof_mt_control[_Alignof(mt_control)];
unsigned char sizeof_mt_control_io[sizeof(mt_control_io)];
unsigned char alignof_mt_control_io[_Alignof(mt_control_io)];
unsigned char sizeof_mt_control_status[sizeof(mt_control_status)];
unsigned char alignof_mt_control_status[_Alignof(mt_control_status)];
unsigned char sizeof_mt_adc_control[sizeof(mt_adc_control)];
unsigned char alignof_mt_adc_control[_Alignof(mt_adc_control)];
unsigned char sizeof_mt_acq_t[sizeof(mt_acq_t)];
unsigned char alignof_mt_acq_t[_Alignof(mt_acq_t)];
unsigned char sizeof_mt_tx[sizeof(mt_tx)];
unsigned char alignof_mt_tx[_Alignof(mt_tx)];
unsigned char sizeof_mt_session[sizeof(mt_session)];
unsigned char alignof_mt_session[_Alignof(mt_session)];
unsigned char sizeof_mt_stream[sizeof(mt_stream)];
unsigned char alignof_mt_stream[_Alignof(mt_stream)];
unsigned char sizeof_mt_wire_message[sizeof(mt_wire_message)];
unsigned char alignof_mt_wire_message[_Alignof(mt_wire_message)];
unsigned char sizeof_mt_command[sizeof(mt_command)];
unsigned char alignof_mt_command[_Alignof(mt_command)];
unsigned char sizeof_mt_command_support[sizeof(mt_command_support)];
unsigned char alignof_mt_command_support[_Alignof(mt_command_support)];
unsigned char sizeof_mt_capture_link[sizeof(mt_capture_link)];
unsigned char alignof_mt_capture_link[_Alignof(mt_capture_link)];
unsigned char sizeof_mt_adc_dma_t[sizeof(mt_adc_dma_t)];
unsigned char alignof_mt_adc_dma_t[_Alignof(mt_adc_dma_t)];
unsigned char sizeof_mt_adc_dma_block_t[sizeof(mt_adc_dma_block_t)];
unsigned char alignof_mt_adc_dma_block_t[_Alignof(mt_adc_dma_block_t)];
unsigned char sizeof_mt_tx_event[sizeof(mt_tx_event)];
unsigned char alignof_mt_tx_event[_Alignof(mt_tx_event)];
unsigned char sizeof_resource_owner_model[sizeof(resource_owner_model)];
unsigned char alignof_resource_owner_model[_Alignof(resource_owner_model)];
unsigned char offset_plus_one_control[offsetof(resource_owner_model, control)+1];
unsigned char offset_plus_one_driver[offsetof(resource_owner_model, driver)+1];
unsigned char offset_plus_one_binding[offsetof(resource_owner_model, binding)+1];
unsigned char offset_plus_one_rx_stream[offsetof(resource_owner_model, rx_stream)+1];
unsigned char offset_plus_one_rx_message[offsetof(resource_owner_model, rx_message)+1];
