const path = require('path');
const CopyPlugin = require('copy-webpack-plugin');

module.exports = {
  // Компилируем все JS/TS модули как отдельные файлы
  entry: {
    'js/common': './js/common.js',
    'js/core/store': './js/core/store.ts',
    'js/core/local_cache': './js/core/local_cache.ts',
    'js/core/websocket': './js/core/websocket.ts',
    'js/core/event_manager': './js/core/event_manager.ts',
    'js/core/token-manager': './js/core/token-manager.js',
    'js/core/map': './js/core/map.js',
    'js/core/data': './js/core/data.js',
    'js/core/ui': './js/core/ui.js',
    'js/modules/popups': './js/modules/popups.js',
    'js/modules/notifications': './js/modules/notifications.js',
  },
  output: {
    filename: '[name].js',
    path: path.resolve(__dirname, 'dist'),
    clean: true,
    publicPath: '/'
  },
  module: {
    rules: [
      {
        test: /\.ts$/,
        use: {
          loader: 'ts-loader',
          options: {
            transpileOnly: true,
            compilerOptions: {
              strict: false,
              noEmit: false
            }
          }
        },
        exclude: /node_modules/
      },
      {
        test: /\.js$/,
        exclude: /node_modules/,
        use: {
          loader: 'babel-loader',
          options: {
            presets: ['@babel/preset-env']
          }
        }
      }
    ]
  },
  resolve: {
    extensions: ['.ts', '.js']
  },
  devtool: 'source-map',
  devServer: {
    static: {
      directory: path.join(__dirname, '/')
    },
    compress: true,
    port: 3000,
    hot: true,
    proxy: {
      '/api': 'http://localhost:8080',
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true
      }
    }
  },
  optimization: {
    minimize: true,
    // Each entry is loaded as a standalone <script> tag (no webpack chunk
    // loader on the page). Splitting would extract shared/vendor code (e.g.
    // zustand) into a separate chunk file that nothing loads — keep every
    // entry fully self-contained.
    splitChunks: false,
    runtimeChunk: false
  },
  plugins: [
    new CopyPlugin({
      patterns: [
        // Копируем только HTML файлы
        { from: 'index.html', to: '.' },
        { from: 'map.html', to: '.' }
      ]
    })
  ]
};
