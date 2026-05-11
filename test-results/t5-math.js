// T5.1: Math verification across all visible rows
(function(){
    var headers = [];
    document.querySelectorAll('table.table thead th').forEach(function(th){ headers.push(th.textContent.trim()); });
    var rows = document.querySelectorAll('table.table tbody tr');
    var data = [];
    rows.forEach(function(row){
        var cells = row.querySelectorAll('td');
        var obj = {};
        for(var i=0; i<cells.length; i++){
            obj[headers[i]] = cells[i].textContent.trim();
        }
        data.push(obj);
    });
    function parseAbs(s) {
        if (!s) return 0;
        var digits = '';
        for (var i = 0; i < s.length; i++) {
            var c = s.charAt(i);
            if ((c >= '0' && c <= '9') || c === '.') digits += c;
        }
        return parseFloat(digits) || 0;
    }
    var errors = [];
    data.forEach(function(d, idx){
        var before = parseAbs(d['前餘額']);
        var issued = parseAbs(d['增加']);
        var used = parseAbs(d['使用']);
        var after = parseAbs(d['後餘額']);
        var expected = before + issued - used;
        if(Math.abs(expected - after) > 0.01) {
            errors.push({row: idx, b: before, i: issued, u: used, a: after, e: expected});
        }
    });
    return JSON.stringify({pass: errors.length === 0, errors: errors, total: data.length});
})()
