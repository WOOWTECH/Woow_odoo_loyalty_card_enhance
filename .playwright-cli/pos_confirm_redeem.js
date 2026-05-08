async (page) => {
  // Click the confirm button in the modal footer
  const clickResult = await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    if (!m) return "no modal";
    const footer = m.querySelector(".modal-footer");
    if (!footer) return "no footer";
    const confirmBtn = footer.querySelector(".btn-primary");
    if (!confirmBtn) return "no confirm button";
    confirmBtn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    return "clicked confirm: " + confirmBtn.textContent.trim();
  });
  // Wait for modal to close and order to update
  await page.waitForTimeout(2000);
  // Check if modal is still open
  const modalCheck = await page.evaluate(() => {
    return document.querySelectorAll(".modal.d-block").length;
  });
  // Check for notifications
  const notifications = await page.evaluate(() => {
    const notifs = document.querySelectorAll(".o_notification");
    return [...notifs].map(n => n.textContent.trim().substring(0, 200));
  });
  // Check order lines
  const orderLines = await page.evaluate(() => {
    const lines = document.querySelectorAll(".orderline");
    return [...lines].map(l => l.textContent.trim().substring(0, 200));
  });
  return JSON.stringify({ clickResult, modalOpen: modalCheck, notifications, orderLines }, null, 2);
}
