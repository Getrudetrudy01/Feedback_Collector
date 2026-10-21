// REPLACE with your actual API Gateway URL
const apiUrl = "https://qorp0jyrl2.execute-api.us-east-1.amazonaws.com/feedback";

const form = document.getElementById('feedbackForm');
const submitBtn = document.getElementById('submitBtn');
const btnText = document.getElementById('btnText');
const btnLoader = document.getElementById('btnLoader');
const messageDiv = document.getElementById('messageDiv');

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    // Disable button and show loader
    submitBtn.disabled = true;
    btnText.textContent = 'Submitting...';
    btnLoader.classList.remove('hidden');
    messageDiv.classList.remove('show');
    
    // Collect form data
    const data = {
        name: document.getElementById('name').value,
        email: document.getElementById('email').value,
        message: document.getElementById('message').value,
        rating: Number(form.querySelector('input[name="rating"]:checked').value)
    };

    try {
        const response = await fetch(apiUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });
        
        const result = await response.json();

        if (response.ok) {
            showMessage('✓ Feedback submitted successfully!', 'success');
            form.reset();
        } else if (response.status === 400) {
            // Validation error: show the server's reason
            showMessage(`✗ ${result.message}`, 'error');
        } else {
            throw new Error(result.message || 'Submission failed');
        }
    } catch (error) {
        showMessage('✗ Failed to submit feedback. Please try again.', 'error');
        console.error('Error submitting feedback:', error);
    } finally {
        // Reset button
        submitBtn.disabled = false;
        btnText.textContent = 'Submit';
        btnLoader.classList.add('hidden');
    }
});

function showMessage(text, type) {
    messageDiv.textContent = text;
    messageDiv.className = `message ${type} show`;
    
    setTimeout(() => {
        messageDiv.classList.remove('show');
    }, 5000);
}