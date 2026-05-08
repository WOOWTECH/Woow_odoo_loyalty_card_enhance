async (page) => {
  // Click + button (index 1) for the first/only line
  await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    const body = m.querySelector(".modal-body");
    const btns = body.querySelectorAll("button");
    btns[1].dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  });
  await page.waitForTimeout(500);

  // Verify input is now 1
  const inputVal = await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    const inp = m.querySelector(".modal-body input");
    return inp.value;
  });

  // Click confirm
  await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    const footer = m.querySelector(".modal-footer");
    const confirmBtn = footer.querySelector(".btn-primary");
    confirmBtn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  });
  await page.waitForTimeout(2000);

  // Check result
  const result = await page.evaluate(() => {
    const modals = document.querySelectorAll(".modal.d-block").length;
    const notifs = [...document.querySelectorAll(".o_notification")].map(n => n.textContent.trim().substring(0, 200));
    const orderLines = [...document.querySelectorAll(".orderline")].map(l => l.textContent.trim().substring(0, 200));
    return JSON.stringify({ inputVal: null, modalOpen: modals, notifications: notifs, orderLines }, null, 2);
  });
  return JSON.stringify({ inputAfterClick: inputVal }) + "\n" + result;
}
