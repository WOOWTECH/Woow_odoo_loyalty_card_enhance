async (page) => {
  // Click + on line 1 (button idx 1 = plus for matcha)
  await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    const body = m.querySelector(".modal-body");
    const btns = body.querySelectorAll("button");
    btns[1].dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  });
  await page.waitForTimeout(300);

  // Click + on line 2 (button idx 3 = plus for coffee)
  await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    const body = m.querySelector(".modal-body");
    const btns = body.querySelectorAll("button");
    btns[3].dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  });
  await page.waitForTimeout(300);

  // Click + on line 2 again (2 coffees)
  await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    const body = m.querySelector(".modal-body");
    const btns = body.querySelectorAll("button");
    btns[3].dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  });
  await page.waitForTimeout(500);

  // Check state before confirming
  const beforeConfirm = await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    const body = m.querySelector(".modal-body");
    const inputs = body.querySelectorAll("input");
    const inputValues = [...inputs].map(i => i.value);
    const footer = m.querySelector(".modal-footer");
    const confirmBtn = footer.querySelector(".btn-primary");
    return JSON.stringify({
      inputValues,
      confirmEnabled: !confirmBtn.disabled,
      confirmText: confirmBtn.textContent.trim()
    });
  });
  return beforeConfirm;
}
