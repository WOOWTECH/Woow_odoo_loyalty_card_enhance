async (page) => {
  const code = "0440-6907-4d9f";
  for (const ch of code) {
    await page.keyboard.press(ch, { delay: 10 });
  }
  await page.keyboard.press("Enter");
  await page.waitForTimeout(2000);
  const modal = await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    if (!m) return "no modal";
    const title = m.querySelector(".modal-title");
    return title ? title.textContent.trim() : m.textContent.substring(0, 200);
  });
  return modal;
}
