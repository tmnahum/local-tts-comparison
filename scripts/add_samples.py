"""Add original tone-comparison articles, optionally queue their Kokoro readings."""
import argparse
import httpx

SAMPLES = [
    {
        'title': 'Humor / The meeting that could have been a sandwich',
        'text': '''The invitation said the meeting would be brief, which was our first warning. It also said we should bring our whole selves to work. I brought a notebook and half a banana. There are limits to what you can carry on the bus.

At nine o'clock, Martin shared a slide titled “Why We Are Here.” This seemed ambitious. Philosophers had spent centuries on the question, and Martin had allowed four minutes. He clicked forward before anyone could object. The next slide was a photograph of a mountain, presumably to remind us that things could be worse.

“We're looking for fresh perspectives,” he said. Priya suggested moving the meeting outside. “Not that kind of perspective.” Someone asked whether we could make a decision today. Martin smiled in the patient way people smile at a dog that has learned to open a cupboard.

By ten, we had agreed to agree on a framework for future agreement. This was written down as progress. My banana had become a structural concern. Across the table, Dan was making the face of a man who had muted himself in real life and could no longer find the button.

Then the lights went out. For three perfect seconds, nobody could see the slides. The room became calm. We heard a pigeon on the windowsill, a bus changing gears, and the quiet sound of fourteen people remembering that they had other things to do. “Perhaps,” Priya said, “we should take this as a sign.”

We finished in the corridor. Without a screen, the decision took forty seconds. Martin sent a summary that afternoon, thanking everyone for a productive session. I read it while eating a sandwich. For once, the sandwich and I were completely aligned.'''
    },
    {
        'title': 'Suspense / The light in the empty station',
        'text': '''At eleven forty-seven, the last train passed without stopping. Nora watched its red lights shrink into the rain, then checked the departure board. Every line was blank. She had been told the station closed at midnight. Nobody had mentioned what happened to the people still inside.

The loudspeaker clicked. “Please remain behind the yellow line.” The voice was calm, almost friendly. There was no train coming. Nora looked down. She was standing well behind the line. At the far end of the platform, a light came on in a room she had thought was empty.

She tried the main doors. Locked. Beside them, a notice displayed a telephone number, but three digits had faded into the paper. Her phone had one percent of its battery left. She lifted it toward the notice anyway, as though better light could bring the missing numbers back.

Footsteps sounded above her. Two slow steps, a pause, then three more. “Hello?” she called. The footsteps stopped. Somewhere behind the ticket office, a chair scraped across the floor. Nora put her phone in her pocket. She did not want whoever was there to see the screen shaking in her hand.

The door at the end of the platform opened. A small woman in an oversized raincoat stepped out, carrying a torch and a paper cup. “You missed the announcement,” she said. Nora tried to answer, but the woman raised a hand. “Don't worry. I have the keys. The power keeps cutting out, and the doors lock themselves.”

Outside, the rain smelled of warm pavement. The woman offered Nora the paper cup. Tea, still hot. “Happens every Thursday,” she said. Nora laughed too loudly, then stopped. Across the road, the station loudspeaker clicked again. This time, with nobody left on the platform, it said her name.'''
    },
    {
        'title': 'Science / A telescope made of time',
        'text': '''Imagine pointing a telescope at a distant galaxy. The image arrives now, but the light began its journey long before you opened your eyes. A telescope gathers light from far away; because light travels at a finite speed, it also lets us look into the past. Distance becomes a kind of history.

The effect begins close to home. Sunlight takes about eight minutes to reach Earth. When you look at the Sun's reflection on a window, you are seeing light that left its surface minutes earlier. The Moon is much closer, so its light reaches us in a little over a second. Neither view is quite live.

Astronomers use a light-year as a measure of distance, not time. It is the distance light travels in one year: roughly 9.46 trillion kilometers. A star ten light-years away appears as it was about ten years ago. The phrase sounds abstract until you imagine a message taking an entire childhood to cross the gap.

There is a complication. The universe expands while the light is traveling. Over very large distances, the galaxy's present distance is therefore not simply its light's travel time multiplied by the speed of light. The space between galaxies changes during the journey, stretching the light along with it.

That stretching shifts light toward longer wavelengths. Astronomers call the effect cosmological redshift. It helps them study expansion and decide which instruments can detect a distant object. A galaxy that emitted much of its light at visible wavelengths may now be easier to observe in infrared.

The result is a sky filled with different moments. A nearby planet, a familiar star, and a faint galaxy do not show us the same date. They arrive together in a single image, but each carries a different piece of the past. Looking up is not just looking outward. It is watching the universe remember.'''
    },
]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args()
    with httpx.Client(base_url='http://127.0.0.1:28765', timeout=30) as client:
        for sample in SAMPLES:
            response = client.post('/api/articles', json=sample)
            response.raise_for_status()
            doc = response.json()
            if args.render:
                response = client.post(f"/api/articles/{doc['id']}/renditions", json={'model': 'kokoro'})
                response.raise_for_status()
            print(doc['id'], doc['title'])
