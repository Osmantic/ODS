document.querySelectorAll('.cloud').forEach(button => {
  const cloud = document.createElement('span');
  cloud.dataset.pixelInteractive = '';
  button.append(cloud);
  PixelMascot.mount(cloud, {state:button.dataset.state});
  button.addEventListener('click', () => PixelMascot.play(cloud));
});
