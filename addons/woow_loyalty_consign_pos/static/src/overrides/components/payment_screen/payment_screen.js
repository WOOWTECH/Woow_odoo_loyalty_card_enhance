/** @odoo-module **/

// Consignment authorization and capture are performed atomically by the
// server-side pos.order._process_order() adapter.  No post-payment RPC is
// allowed here: a second frontend confirmation path could leave a paid order
// uncovered when it fails after the native POS sync already succeeded.
