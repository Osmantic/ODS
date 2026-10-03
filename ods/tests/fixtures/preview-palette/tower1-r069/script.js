document.addEventListener('DOMContentLoaded', function() {
  const showSoldOutBtn = document.getElementById('show-sold-out-btn');
  const midnightCard = document.getElementById('midnight-concert-card');

  if (!showSoldOutBtn || !midnightCard) {
    console.error('Required elements not found');
    return;
  }

  showSoldOutBtn.addEventListener('click', function() {
    midnightCard.hidden = false;
    midnightCard.setAttribute('aria-hidden', 'false');
    
    // Update button text and make it visually indicate the action is complete
    showSoldOutBtn.textContent = 'Hide sold out';
    showSoldOutBtn.setAttribute('aria-expanded', 'false');
    
    // Add a subtle animation class for the reveal
    midnightCard.style.animation = 'fadeIn 0.5s ease-out';
  });

  // Add keyboard accessibility - Enter and Space to activate
  showSoldOutBtn.addEventListener('keydown', function(event) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      showSoldOutBtn.click();
    }
  });
});

// Add fadeIn animation dynamically
const style = document.createElement('style');
style.textContent = `
  @keyframes fadeIn {
    from {
      opacity: 0;
      transform: translateY(-10px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
`;
document.head.appendChild(style);
