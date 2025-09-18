(function(){
  function $(sel, root){ return (root||document).querySelector(sel); }
  function $all(sel, root){ return Array.prototype.slice.call((root||document).querySelectorAll(sel)); }
  function closestLI(el){ while (el && el.nodeType === 1){ if (el.tagName === 'LI') return el; el = el.parentElement; } return null; }

  var form = $('#tree-bulk-move-form');
  if (!form) return;
  var targetTypeInput = form.querySelector('input[name="target_type"]');
  var targetIdInput = form.querySelector('input[name="target_id"]');

  // clear any previous destination highlight on load
  $all('li.dest').forEach(function(li){ li.classList.remove('dest'); });

  function setDestinationFromCheckbox(cb){
    var li = closestLI(cb);
    if (!li) return;
    var type = cb.getAttribute('data-item-type');
    var id = cb.value;
    if (!type || !id) return;
    // set hidden inputs
    if (targetTypeInput) targetTypeInput.value = type;
    if (targetIdInput) targetIdInput.value = id;
    // update highlight
    $all('li.dest').forEach(function(el){ el.classList.remove('dest'); });
    li.classList.add('dest');
    // ensure destination is not also in the multi-select set for sanity (optional)
    try{ cb.checked = true; } catch(e){}
  }

  // Double-click handler: mark destination
  $all('input.tree-select').forEach(function(cb){
    cb.addEventListener('dblclick', function(ev){
      setDestinationFromCheckbox(cb);
      ev.preventDefault();
      ev.stopPropagation();
    });
  });
})();
