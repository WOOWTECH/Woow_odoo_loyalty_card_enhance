/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

async function updateConsignAllocation(button, quantity) {
    const option = button.closest(".o_consign_cart_option");
    const controls = button.closest("#o_consign_cart_controls");
    const status = controls?.querySelector(".js_consign_status");
    const buttons = option?.querySelectorAll("button") || [];
    for (const current of buttons) {
        current.disabled = true;
    }
    if (status) {
        status.classList.add("d-none");
        status.textContent = "";
    }
    try {
        await rpc("/shop/consign/allocation", {
            card_id: Number(option.dataset.cardId),
            product_id: Number(option.dataset.productId),
            quantity,
        });
        window.location.reload();
    } catch (error) {
        if (status) {
            status.textContent = error?.data?.message || error?.message || "Unable to update the consignment balance.";
            status.classList.remove("d-none");
        }
        for (const current of buttons) {
            current.disabled = false;
        }
    }
}

document.addEventListener("click", async (event) => {
    const applyButton = event.target.closest(".js_consign_apply");
    const removeButton = event.target.closest(".js_consign_remove");
    const button = applyButton || removeButton;
    if (!button) {
        return;
    }
    event.preventDefault();
    const option = button.closest(".o_consign_cart_option");
    const input = option?.querySelector(".js_consign_quantity");
    const quantity = removeButton ? 0 : Number(input?.value || 0);
    if (!Number.isFinite(quantity) || quantity < 0) {
        return;
    }
    await updateConsignAllocation(button, quantity);
});
