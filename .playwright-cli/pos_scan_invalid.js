async (page) => {
  const code = "XXXX-YYYY-ZZZZ";
  for (const ch of code) {
    await page.keyboard.press(ch, { delay: 10 });
  }
  await page.keyboard.press("Enter");
  await page.waitForTimeout(2000);
  const result = await page.evaluate(() => {
    const m = document.querySelector(".modal.d-block");
    const notifs = [...document.querySelectorAll(".o_notification")].map(n => n.textContent.trim().substring(0, 200));
    if (m) {
      const title = m.querySelector(".modal-title");
      return JSON.stringify({ modal: title ? title.textContent.trim() : "modal without title", notifications: notifs });
    }
    return JSON.stringify({ modal: null, notifications: notifs });
  });
  return result;
}
