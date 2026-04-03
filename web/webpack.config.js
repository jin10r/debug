const path = require('path');

module.exports = {
  // Компилируем все JS/TS модули как отдельные файлы
  entry: {
    'js/common': './js/common.js',
    'js/core/store': './js/core/store.ts',
    'js/core/local_cache': './js/core/local_cache.ts',
    'js/core/websocket': './js/core/websocket.ts',
    'js/core/event_manager': './js/core/event_manager.ts',
    'js/telegram/validator': './js/telegram/validator.ts',
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
    splitChunks: {
      chunks: 'all'
    }
  }
};
