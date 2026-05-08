async (page) => {
  const result = await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    if (!m) return "no modal";
    const body = m.querySelector(".modal-body");
    if (!body) return "no body";
    // Find all buttons
    const btns = body.querySelectorAll("button");
    const btnData = [...btns].map((b, i) => ({
      idx: i,
      text: b.textContent.trim().substring(0, 50),
      classes: b.className,
      disabled: b.disabled
    }));
    // Find all input fields
    const inputs = body.querySelectorAll("input");
    const inputData = [...inputs].map((inp, i) => ({
      idx: i,
      value: inp.value,
      type: inp.type,
      classes: inp.className
    }));
    // Get text content summary
    const textContent = body.textContent.replace(/\s+/g, " ").trim().substring(0, 500);
    return JSON.stringify({ buttons: btnData, inputs: inputData, text: textContent }, null, 2);
  });
  return result;
}
