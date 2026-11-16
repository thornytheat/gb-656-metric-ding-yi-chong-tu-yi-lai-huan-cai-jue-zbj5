/* 脚注：按出现顺序编号，并链到页脚条目 */
(function () {
  const notes = Array.from(document.querySelectorAll('.footnotes li'));
  const marks = Array.from(document.querySelectorAll('sup.fn'));

  marks.forEach((mark, position) => {
    const number = position + 1;
    const note = notes[position];
    if (note) {
      if (!note.id) {
        note.id = `fn${number}`;
      }
      const link = document.createElement('a');
      link.href = `#${note.id}`;
      link.className = 'fn-link';
      link.textContent = String(number);
      mark.textContent = '';
      mark.appendChild(link);
    } else {
      mark.textContent = String(number);
    }
  });

  notes.forEach((note) => {
    const back = note.querySelector('.back');
    if (back) {
      back.setAttribute('aria-label', '回到正文');
    }
  });
})();
