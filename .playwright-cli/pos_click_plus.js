async (page) => {
  // Get all buttons in the modal body - button[1] is the + for the first line
  const result = await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    if (!m) return "no modal";
    const body = m.querySelector(".modal-body");
    if (!body) return "no body";
    const btns = body.querySelectorAll("button");
    // Button index 1 = plus for first Margaux line
    const plusBtn = btns[1];
    if (!plusBtn) return "no plus button";
    plusBtn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    // Wait a tick for OWL reactivity
    return "clicked plus button 1";
  });
  // Wait for OWL to update
  await page.waitForTimeout(500);
  // Check updated state
  const state = await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    if (!m) return "no modal";
    const body = m.querySelector(".modal-body");
    const inputs = body.querySelectorAll("input");
    const inputValues = [...inputs].map(i => i.value);
    const footer = m.querySelector(".modal-footer");
    const footerBtns = footer ? [...footer.querySelectorAll("button")].map(b => ({
      text: b.textContent.trim(),
      disabled: b.disabled,
      classes: b.className
    })) : [];
    return JSON.stringify({ inputValues, footerBtns }, null, 2);
  });
  return state;
}
