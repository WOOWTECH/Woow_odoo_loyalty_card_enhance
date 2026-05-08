async (page) => {
  // First check current state
  const before = await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    const body = m.querySelector(".modal-body");
    const inputs = body.querySelectorAll("input");
    return [...inputs].map(i => i.value);
  });

  // Click + on line 1 (button idx 1) multiple times - line 1 has only 1 remaining
  const plusClicks = [];
  for (let i = 0; i < 3; i++) {
    await page.evaluate(() => {
      const m = document.querySelector(".modal.d-block");
      const body = m.querySelector(".modal-body");
      const btns = body.querySelectorAll("button");
      btns[1].dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    });
    await page.waitForTimeout(300);
    const val = await page.evaluate(() => {
      const m = document.querySelector(".modal.d-block");
      const inp = m.querySelector(".modal-body input");
      return inp.value;
    });
    plusClicks.push(val);
  }

  return JSON.stringify({ before, afterEachClick: plusClicks }, null, 2);
}
