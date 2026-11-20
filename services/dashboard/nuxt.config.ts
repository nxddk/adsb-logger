export default defineNuxtConfig({
  compatibilityDate: '2026-11-12',
  devtools: { enabled: false },
  css: ['~/assets/main.css'],
  nitro: { preset: 'node-server' },
  app: {
    head: {
      title: 'Airspace — ADS-B recorder',
      meta: [
        {
          name: 'description',
          content: 'Start, stop, and monitor OpenSky recording sessions.',
        },
      ],
      htmlAttrs: { lang: 'en' },
      link: [{ rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' }],
    },
  },
})
